"""
SenteFlow AI — ActionDispatcher (v7)
======================================
Maps a BusinessEvent to typed tools and runs them.

IDEA 01 — Business Tool System:
  Actions are ToolBase subclasses with required_entities validation. If
  required entities are missing, the tool returns ask_clarification=True and
  the owner gets a specific question instead of a broken confirmation.

IDEA 09 — Permission System:
  Before each tool runs, OrgConfig decides whether it needs the owner's
  approval (reduce_debt always; update_ledger above UGX 500,000). The
  dispatcher then stops and returns the approval question. When the owner
  replies YES, the router calls dispatch() again with the action in
  `approved_actions` and the already-finished actions in `skip_actions`, so
  the event resumes exactly where it paused.

v7 fixes:
  - Tools are chosen deterministically from the event type. The LLM's
    suggestions are stored for audit but no longer decide what runs (they
    usually omitted create_order and named tools that don't exist).
  - Records are keyed on the CUSTOMER the message is about (event.customer_id),
    never on the WhatsApp sender — the sender is always the business owner.
  - reduce_debt actually closes the customer's open promises and reminders.
  - Ledger entries recorded by the owner over WhatsApp are approved entries
    (the owner is the approver), so they show up on the dashboard.
"""

import logging
import inspect
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, Optional, Iterable

from utils.clock import utc_now

from domain.events.business_event import BusinessEvent, EventResult, ProcessingStatus
from domain.events.event_types import EventType
from services.memory.memory_engine import BusinessMemoryEngine

if TYPE_CHECKING:
    from repositories.transaction_repository import TransactionRepository

logger = logging.getLogger(__name__)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


# ─── Tool result ──────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    outcome: str
    ask_clarification: bool = False
    clarification_question: str = ""
    pending_approval: bool = False
    approval_question: str = ""

    def is_ok(self) -> bool:
        return not self.ask_clarification and not self.pending_approval


# ─── Due-date display ─────────────────────────────────────────────────────────

def _resolve_due_date(raw_due: Any) -> str:
    """Human-friendly due date for replies ("Friday", "Tue 03 Jun", "soon")."""
    from datetime import datetime, timedelta
    if not raw_due:
        return "soon"
    s = str(raw_due).strip().lower()
    if s == "tomorrow":
        dt = utc_now() + timedelta(days=1)
        return dt.strftime("%a %d %b")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%a %d %b")
        except ValueError:
            pass
    # Title-case the normalised string, not raw_due — raw_due can be an int or
    # date object, and .strip() on those raises.
    return s.title()


def _customer_label(event: BusinessEvent, *keys: str) -> str:
    entities = event.entities or {}
    for key in ("customer", *keys):
        value = entities.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return event.customer_name or "Someone"


def _whatsapp_approver(event: BusinessEvent) -> str:
    return f"whatsapp:{(event.sender_id or '').split('@')[0]}"


# ─── Base tool class (IDEA 01) ────────────────────────────────────────────────

class ToolBase:
    """
    All actions are typed tool classes with:
    - name: str
    - required_entities: list[str]  → missing = clarification question
    - execute(): the actual implementation
    - is_applicable(): False → the tool is skipped (and never asks for approval)
    """
    name: str = ""
    required_entities: list[str] = []
    optional_entities: list[str] = []
    clarification_question: str = "Could you provide more details?"

    def _check_required(self, event: BusinessEvent) -> Optional[ToolResult]:
        """Return a clarification ToolResult if any required entity is missing."""
        missing = [
            k for k in self.required_entities
            if not event.entities.get(k)
        ]
        if missing:
            return ToolResult(
                outcome=f"{self.name}:missing_entities:{','.join(missing)}",
                ask_clarification=True,
                clarification_question=self.clarification_question,
            )
        return None

    async def is_applicable(self, event: BusinessEvent, repo, org_id: str) -> bool:
        return True

    async def execute(
        self,
        event: BusinessEvent,
        repo: "TransactionRepository",
        org_id: str,
    ) -> ToolResult:
        raise NotImplementedError


# ─── Concrete tool implementations ───────────────────────────────────────────

_LEDGER_TYPES = {
    EventType.PAYMENT_RECEIVED: "payment",
    EventType.EXPENSE_RECORDED: "expense",
    EventType.DEBT_CREATED: "debt_created",
    EventType.PAYMENT_PROMISE: "payment_promise",
    EventType.PAYMENT: "payment",
    EventType.INCOME: "payment",
}


class UpdateLedgerTool(ToolBase):
    name = "update_ledger"
    required_entities = ["amount"]
    optional_entities = ["currency", "category", "payer", "payee"]
    clarification_question = "How much was the transaction? e.g. 'Brian paid UGX 50,000'"

    async def execute(self, event, repo, org_id) -> ToolResult:
        check = self._check_required(event)
        if check:
            return check

        entities = event.entities
        amount = entities.get("amount")

        try:
            txn_type = _LEDGER_TYPES.get(event.event_type, "payment")
            payer = entities.get("payer") or entities.get("sender")
            if txn_type == "payment" and not payer:
                payer = entities.get("customer")

            from domain.models import Transaction
            txn = Transaction(
                amount=float(amount),
                currency=entities.get("currency", "UGX"),
                transaction_type=txn_type,
                category=entities.get("category", "other"),
                description=entities.get("item") or entities.get("description") or event.raw_message[:100],
                payer=payer,
                payee=entities.get("payee") or entities.get("supplier"),
                notes=f"[BusinessEvent:{event.event_id}]",
                # The owner recorded this themselves on WhatsApp — that is the
                # approval. Leaving it "pending" hid every WhatsApp entry from
                # the dashboard, which only lists approved records.
                status="approved",
                approved_by=_whatsapp_approver(event),
                approved_at=utc_now().isoformat(),
                customer_id=event.customer_id,
                customer_name=event.customer_name or entities.get("customer"),
                debtor=entities.get("debtor") if txn_type == "debt_created" else None,
                due_date=entities.get("due_date"),
                source="whatsapp",
                source_event_id=event.event_id,
            )
            txn_id = await _maybe_await(repo.save_transaction(org_id, txn.model_dump(), event.sender_id))
            event.transaction_id = txn_id
            return ToolResult(outcome=f"update_ledger:ok:{txn_id}")
        except Exception as exc:
            logger.error("ledger_update_failed", extra={"error": str(exc)})
            return ToolResult(outcome=f"update_ledger:failed:{exc}")


class ReduceDebtTool(ToolBase):
    """
    A payment came in from a customer: close the open promises/reminders it
    settles.

    The money itself is already in the ledger via update_ledger; balances are
    computed from the ledger (debts − payments). This tool only closes
    commitments, and only asks the owner when the payment actually settles
    one — a partial payment leaves them open without a question.
    """
    name = "reduce_debt"
    required_entities = []
    optional_entities = ["amount", "debtor"]

    async def _open_items(self, event, repo, org_id) -> Optional[list[dict]]:
        finder = getattr(repo, "list_open_commitments", None)
        if finder is None:
            return None  # cannot check → behave as before (ask)
        name = _customer_label(event, "payer", "debtor")
        return await _maybe_await(finder(org_id, customer_id=event.customer_id, name=name))

    async def _outstanding_after_payment(self, event, repo, org_id) -> Optional[float]:
        """Customer's ledger balance after this payment (None if unknown)."""
        if not event.customer_id or not hasattr(repo, "list_transactions"):
            return None
        try:
            from repositories.transaction_repository import SUMMARY_EVENT_LIMIT
            from services.responses.reply_generator import compute_customer_balances
            txns = await _maybe_await(repo.list_transactions(org_id, limit=SUMMARY_EVENT_LIMIT))
            entry = compute_customer_balances(txns).get(event.customer_id)
            if not entry or entry["owed"] <= 0:
                return None  # no recorded debt — promises only
            return entry["balance"]
        except Exception as exc:
            logger.warning("reduce_debt_balance_failed", extra={"error": str(exc)})
            return None

    async def _plan(self, event, repo, org_id) -> Optional[list[dict]]:
        """Which open items this payment settles. None = can't tell."""
        items = await self._open_items(event, repo, org_id)
        if items is None:
            return None
        if not items:
            return []

        # A recorded debt that is now fully paid closes everything for them.
        balance = await self._outstanding_after_payment(event, repo, org_id)
        if balance is not None:
            return list(items) if balance <= 0.5 else []

        # Promises only: close those this payment covers, oldest first.
        try:
            paid = float(event.entities.get("amount") or 0)
        except (TypeError, ValueError):
            paid = 0.0
        remaining = paid
        chosen_ids: set = set()
        for item in sorted(items, key=lambda i: str(i.get("created_at") or "")):
            if item.get("id") in chosen_ids:
                continue
            amount = item.get("amount")
            if amount is None:
                amount = (item.get("entities") or {}).get("amount")
            try:
                amount = float(amount) if amount is not None else None
            except (TypeError, ValueError):
                amount = None
            if amount is None or amount <= remaining + 0.5:
                chosen_ids.add(item.get("id"))
                if amount:
                    remaining -= amount
        # A promise and its reminder share the event id — close them together.
        return [i for i in items if i.get("id") in chosen_ids]

    async def is_applicable(self, event, repo, org_id) -> bool:
        try:
            plan = await self._plan(event, repo, org_id)
        except Exception as exc:
            logger.warning("reduce_debt_lookup_failed", extra={"error": str(exc)})
            return False
        return plan is None or len(plan) > 0

    async def execute(self, event, repo, org_id) -> ToolResult:
        to_resolve = await self._plan(event, repo, org_id) or []
        if not to_resolve:
            return ToolResult(outcome="reduce_debt:nothing_to_close")
        resolver = getattr(repo, "resolve_commitments", None)
        count = await _maybe_await(
            resolver(org_id, to_resolve, _whatsapp_approver(event), resolved_by_event=event.event_id)
        ) if resolver else 0
        event.entities["_debts_cleared"] = count
        return ToolResult(outcome=f"reduce_debt:ok:{count}")


class ScheduleReminderTool(ToolBase):
    name = "schedule_reminder"
    required_entities = ["debtor"]
    optional_entities = ["due_date", "amount"]
    clarification_question = (
        "Who should I remind you about, and by when? "
        "e.g. 'Remind me Friday that Bruno owes 50k'"
    )
    _PERSON_KEYS = ("debtor", "customer", "payer", "person", "supplier")
    _PAYMENT_TYPES = {EventType.PAYMENT_PROMISE, EventType.DEBT_CREATED}

    def _check_required(self, event: BusinessEvent) -> Optional[ToolResult]:
        if any(event.entities.get(k) for k in self._PERSON_KEYS) or event.customer_name:
            return None
        return ToolResult(
            outcome=f"{self.name}:missing_entities:person",
            ask_clarification=True,
            clarification_question=self.clarification_question,
        )

    async def execute(self, event, repo, org_id) -> ToolResult:
        check = self._check_required(event)
        if check:
            return check

        from utils.dates import reminder_due_at

        entities = event.entities or {}
        due_raw = entities.get("due_date") or entities.get("date")
        due_display = _resolve_due_date(due_raw)
        amount = entities.get("amount")
        debtor = (
            entities.get("debtor")
            or entities.get("customer")
            or entities.get("payer")
            or entities.get("person")
            or entities.get("supplier")
            or event.customer_name
            or "Unknown"
        )
        is_payment = event.event_type in self._PAYMENT_TYPES
        due_at = reminder_due_at(due_raw, after_due_date=is_payment)

        try:
            amount_value = float(amount) if amount else None
        except (TypeError, ValueError):
            amount_value = None

        reminder_doc = {
            "type": "reminder",
            "kind": "payment" if is_payment else "follow_up",
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "due_date_raw": str(due_raw) if due_raw else None,
            "due_date_display": due_display,
            "due_at": due_at.isoformat() if due_at else None,
            "amount": amount_value,
            "currency": entities.get("currency", "UGX"),
            "debtor": debtor,
            "customer_id": event.customer_id,
            "sender_id": event.sender_id,   # the owner — reminders go back to them
            "raw_message": event.raw_message,
            "status": "pending",
            "created_at": utc_now().isoformat(),
            "org_id": org_id,
        }

        try:
            repo._db.collection("organizations").document(org_id) \
                .collection("reminders").document(event.event_id).set(reminder_doc)
        except Exception as exc:
            logger.warning("reminder_store_failed", extra={"error": str(exc)})

        try:
            memory = BusinessMemoryEngine(repo=repo, org_id=org_id)
            memory.remember_event(event)
        except Exception as exc:
            logger.warning("memory_engine_reminder_failed", extra={"error": str(exc)})

        event.entities["_due_display"] = due_display
        return ToolResult(outcome=f"schedule_reminder:ok:{due_display}")


class UpdateCustomerProfileTool(ToolBase):
    name = "update_customer_profile"
    required_entities = []

    async def is_applicable(self, event, repo, org_id) -> bool:
        # Only real customers get a profile. Writing under the sender id made
        # the business owner a single "customer" whose name changed with
        # every message.
        return bool(event.customer_id)

    async def execute(self, event, repo, org_id) -> ToolResult:
        entities = event.entities or {}
        updates: dict[str, Any] = {
            "customer_id": event.customer_id,
            "display_name": event.customer_name or entities.get("customer"),
            "last_event_type": event.event_type.value,
            "last_message": event.raw_message,
            "last_confidence": event.confidence,
            "last_interaction": utc_now().isoformat(),
        }
        if entities.get("amount"):
            updates["last_amount"] = entities["amount"]
        if entities.get("item"):
            updates["last_item"] = entities["item"]
        if entities.get("phone"):
            from services.tenancy import normalize_phone
            updates["phone_number"] = normalize_phone(entities["phone"])
        updates = {k: v for k, v in updates.items() if v is not None}
        await _maybe_await(repo.upsert_customer_profile(org_id, event.customer_id, updates))
        return ToolResult(outcome="update_customer_profile:ok")


class UpdateConversationTool(ToolBase):
    name = "update_conversation"
    required_entities = []

    async def is_applicable(self, event, repo, org_id) -> bool:
        # The inquiry → order → payment flow is per customer.
        return bool(event.customer_id)

    async def execute(self, event, repo, org_id) -> ToolResult:
        from services.conversation import ConversationStateManager, EntityLinker
        manager = ConversationStateManager(repo=repo, org_id=org_id)
        await _maybe_await(manager.apply_business_event(event))
        linker = EntityLinker(repo=repo, org_id=org_id)
        await _maybe_await(linker.link_business_event(event))
        return ToolResult(outcome="update_conversation:ok")


class CreateOrderTool(ToolBase):
    """
    Record a customer order as a proper Order document (the shape the
    /api/orders endpoints read). The old free-form dict used statuses and
    item shapes the Order model rejects, so the orders API failed to parse it.
    """
    name = "create_order"
    required_entities = []
    optional_entities = ["customer", "buyer", "items", "item", "quantity", "amount"]

    async def execute(self, event, repo, org_id) -> ToolResult:
        from domain.orders.model import Order, OrderItem, OrderStatus

        entities = event.entities or {}

        def _num(value, default=None):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        raw_items = entities.get("items")
        items: list[OrderItem] = []
        if isinstance(raw_items, list):
            for it in raw_items:
                if isinstance(it, dict) and it.get("name"):
                    items.append(OrderItem(
                        name=str(it["name"]),
                        quantity=_num(it.get("quantity"), 1.0) or 1.0,
                        unit=str(it.get("unit") or "units"),
                        unit_price=_num(it.get("unit_price")),
                        total_price=_num(it.get("total_price")),
                    ))
                elif it:
                    items.append(OrderItem(name=str(it)))
        elif entities.get("item"):
            items.append(OrderItem(
                name=str(entities["item"]),
                quantity=_num(entities.get("quantity"), 1.0) or 1.0,
                unit=str(entities.get("unit") or "units"),
            ))

        order = Order(
            org_id=org_id,
            customer_id=event.customer_id or "unknown",
            customer_name=event.customer_name or entities.get("customer") or entities.get("buyer"),
            status=OrderStatus.CONFIRMED,
            items=items,
            source_event_id=event.event_id,
            raw_message=event.raw_message,
            promised_payment_date=entities.get("due_date"),
            delivery_date=entities.get("delivery_date"),
            metadata={"recorded_by": event.sender_id, "currency": entities.get("currency", "UGX")},
        )
        amount = _num(entities.get("amount"))
        if amount:
            order.total = amount
            order.subtotal = amount
            order.debt_amount = amount
        elif items:
            order.compute_totals()
        order.add_timeline_entry("confirmed", "Recorded from WhatsApp", actor="business")

        data = order.model_dump(mode="json")
        order_id = await _maybe_await(repo.create_order(org_id, data))
        event.entities["order_id"] = order_id
        return ToolResult(outcome=f"create_order:ok:{order_id}")


class CreateAlertTool(ToolBase):
    name = "create_alert"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        try:
            memory = BusinessMemoryEngine(repo=repo, org_id=org_id)
            memory.remember_event(event)
            return ToolResult(outcome="create_alert:ok")
        except Exception as exc:
            return ToolResult(outcome=f"create_alert:failed:{exc}")


class UpdateInventoryTool(ToolBase):
    """
    Stock was replenished/counted: close any open low-stock alert for the item
    so the briefing and dashboard stop flagging it.
    """
    name = "update_inventory"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        item = str((event.entities or {}).get("item") or "").strip().lower()
        if not item:
            return ToolResult(outcome="update_inventory:no_item")
        closed = 0
        try:
            docs = (
                repo._db.collection("organizations").document(org_id)
                .collection("alerts").where("status", "==", "open").get()
            )
            for d in docs:
                alert_item = str(((d.to_dict() or {}).get("entities") or {}).get("item") or "").strip().lower()
                if alert_item and (alert_item == item or alert_item in item or item in alert_item):
                    d.reference.update({
                        "status": "resolved",
                        "acknowledged_at": utc_now().isoformat(),
                        "acknowledged_by": _whatsapp_approver(event),
                    })
                    closed += 1
        except Exception as exc:
            logger.warning("inventory_alert_close_failed", extra={"error": str(exc)})
        return ToolResult(outcome=f"update_inventory:ok:{closed}")


class NotifyOwnerTool(ToolBase):
    """
    The owner is the person talking to SenteFlow, so the WhatsApp reply IS
    the notification. This tool only records that one was due (for audit).
    """
    name = "notify_owner"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        logger.info("owner_notified_in_reply", extra={"event_id": event.event_id, "type": event.event_type.value})
        return ToolResult(outcome="notify_owner:in_reply")


class RunExtractionWorkflowTool(ToolBase):
    name = "run_extraction_workflow"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        return ToolResult(outcome="run_extraction_workflow:deferred")


# ─── Tool Registry ────────────────────────────────────────────────────────────

_TOOL_REGISTRY: dict[str, ToolBase] = {
    t.name: t for t in [
        UpdateLedgerTool(),
        ReduceDebtTool(),
        ScheduleReminderTool(),
        UpdateCustomerProfileTool(),
        UpdateConversationTool(),
        CreateOrderTool(),
        CreateAlertTool(),
        UpdateInventoryTool(),
        NotifyOwnerTool(),
        RunExtractionWorkflowTool(),
    ]
}

_DEFAULT_ACTIONS: dict[EventType, list[str]] = {
    EventType.PAYMENT_RECEIVED: ["update_ledger", "reduce_debt", "update_customer_profile", "update_conversation"],
    EventType.PAYMENT_PROMISE: ["schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.DEBT_CREATED: ["update_ledger", "schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.EXPENSE_RECORDED: ["update_ledger"],
    EventType.LOW_STOCK_ALERT: ["create_alert"],
    EventType.INVENTORY_UPDATE: ["update_inventory"],
    EventType.CUSTOMER_INQUIRY: ["update_customer_profile", "update_conversation"],
    EventType.CUSTOMER_ORDER: ["create_order", "update_customer_profile", "update_conversation"],
    EventType.ORDER_RECEIVED: ["create_order", "update_customer_profile", "update_conversation"],
    EventType.NEGOTIATION: ["update_customer_profile", "update_conversation"],
    EventType.COMPLAINT: ["schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.APPOINTMENT_REQUEST: ["schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.SUPPLIER_MESSAGE: [],
    EventType.DELIVERY_UPDATE: ["update_customer_profile", "update_conversation"],
    EventType.FOLLOW_UP_REQUIRED: ["schedule_reminder", "update_customer_profile"],
    EventType.REMINDER_REQUEST: ["schedule_reminder", "update_customer_profile"],
    EventType.BUSINESS_NOTE: [],
    EventType.BUSINESS_QUERY: [],
    EventType.UNKNOWN: [],
    EventType.PAYMENT: ["update_ledger", "reduce_debt", "update_customer_profile"],
    EventType.INCOME: ["update_ledger", "reduce_debt", "update_customer_profile"],
}


def default_actions_for(event_type: EventType) -> list[str]:
    return list(_DEFAULT_ACTIONS.get(event_type, []))


# ─── ActionDispatcher ─────────────────────────────────────────────────────────

class ActionDispatcher:
    """
    Maps a BusinessEvent to one or more typed Tool instances and executes them.

    IDEA 01: Tools validate required_entities before executing.
    IDEA 09: Checks OrgConfig permissions before executing each tool.
    """

    def __init__(self, repo: "TransactionRepository", org_id: str, org_config=None):
        self.repo = repo
        self.org_id = org_id
        self.org_config = org_config  # OrgConfig | None — IDEA 09

    async def dispatch(
        self,
        event: BusinessEvent,
        approved_actions: Iterable[str] = (),
        skip_actions: Iterable[str] = (),
    ) -> EventResult:
        """
        Run the event's tools in order.

        approved_actions: action keys the owner already said YES to.
        skip_actions:     action keys that already ran before an approval pause.
        """
        from domain.permissions.model import OrgConfig

        approved = set(approved_actions or ())
        skipped = set(skip_actions or ())

        result = EventResult(
            event_id=event.event_id,
            success=False,
            actions_executed=[],
            actions_failed=[],
            completed_actions=list(skipped),
        )

        action_keys = list(event.recommended_actions or default_actions_for(event.event_type))

        if not action_keys:
            result.success = True
            result.whatsapp_reply = self._build_reply(event, result)
            event.processing_status = ProcessingStatus.COMPLETED
            await self._persist(event)
            return result

        org_config = self.org_config or OrgConfig.default(self.org_id)

        for key in action_keys:
            if key in skipped:
                continue
            tool = _TOOL_REGISTRY.get(key)
            if tool is None:
                logger.warning("unknown_action_key", extra={"key": key})
                result.actions_failed.append(f"{key}:unknown")
                continue

            try:
                if not await tool.is_applicable(event, self.repo, self.org_id):
                    result.actions_executed.append(f"{key}:skipped")
                    result.completed_actions.append(key)
                    continue
            except Exception as exc:
                logger.warning("tool_applicability_check_failed", extra={"action": key, "error": str(exc)})

            # IDEA 09: permission check before execution
            perm = org_config.get_permission(key)
            amount = event.entities.get("amount")
            try:
                amount = float(amount) if amount is not None else None
            except (TypeError, ValueError):
                amount = None
            if key not in approved and perm.requires_approval(amount):
                question = perm.get_approval_question(event.to_summary())
                result.actions_executed.append(f"{key}:pending_approval")
                result.whatsapp_reply = question
                result.pending_approval = True
                result.pending_action = key
                logger.info("action_pending_approval", extra={"action": key, "event_id": event.event_id})
                # Stop further actions — owner must confirm first
                event.processing_status = ProcessingStatus.PENDING
                await self._persist(event)
                return result

            try:
                tool_result = await tool.execute(event, self.repo, self.org_id)

                # IDEA 01: clarification needed — short-circuit with a question
                if tool_result.ask_clarification:
                    result.whatsapp_reply = tool_result.clarification_question
                    result.success = True  # Not a failure — just need more info
                    result.needs_clarification = True
                    result.actions_executed.append(tool_result.outcome)
                    event.processing_status = ProcessingStatus.PENDING
                    await self._persist(event)
                    return result

                result.actions_executed.append(tool_result.outcome)
                result.completed_actions.append(key)
                if event.transaction_id and event.transaction_id not in result.transaction_ids:
                    result.transaction_ids.append(event.transaction_id)
                logger.debug("action_executed", extra={"action": key, "outcome": tool_result.outcome})
            except Exception as exc:
                logger.error("action_failed", extra={"action": key, "error": str(exc)})
                result.actions_failed.append(f"{key}:error:{exc}")

        failed_core = [a for a in result.actions_failed if a.split(":")[0] in ("update_ledger", "create_order", "schedule_reminder")]
        result.success = not failed_core and (len(result.actions_failed) == 0 or len(result.actions_executed) > 0)
        result.followup_scheduled = any(
            a.startswith("schedule_reminder:ok") for a in result.actions_executed
        )
        result.whatsapp_reply = self._build_reply(event, result)

        event.processing_status = (
            ProcessingStatus.COMPLETED if result.success else ProcessingStatus.FAILED
        )
        await self._persist(event)

        logger.info(
            "dispatch_complete",
            extra={
                "event_id": event.event_id,
                "type": event.event_type.value,
                "executed": len(result.actions_executed),
                "failed": len(result.actions_failed),
            },
        )
        return result

    async def _persist(self, event: BusinessEvent) -> None:
        try:
            await _maybe_await(self.repo.save_business_event(self.org_id, event.to_storable()))
        except Exception as exc:
            logger.warning("business_event_persist_failed", extra={"error": str(exc)})

    def _build_reply(self, event: BusinessEvent, result: EventResult) -> str:
        etype = event.event_type
        entities = event.entities
        conf = int(event.confidence * 100)

        failed_ledger = any(a.startswith("update_ledger") for a in result.actions_failed) or any(
            a.startswith("update_ledger:failed") for a in result.actions_executed
        )
        if failed_ledger:
            return "⚠️ I understood that but couldn't save it just now. Please send it again in a moment."

        def _money(value) -> str:
            currency = entities.get("currency", "UGX")
            if isinstance(value, (int, float)):
                return f"{currency} {value:,.0f}"
            return str(value) if value else "?"

        if etype in (EventType.PAYMENT_RECEIVED, EventType.PAYMENT, EventType.INCOME):
            payer = _customer_label(event, "payer")
            reply = f"✅ *Payment recorded*\n{payer} paid {_money(entities.get('amount'))}"
            cleared = entities.get("_debts_cleared")
            if cleared:
                reply += f"\nClosed {cleared} open promise{'s' if cleared != 1 else ''}/reminder{'s' if cleared != 1 else ''}."
            return reply + f"\n_(confidence: {conf}%)_"

        elif etype == EventType.PAYMENT_PROMISE:
            debtor = _customer_label(event, "debtor", "payer")
            due_display = (
                entities.get("_due_display")
                or _resolve_due_date(entities.get("due_date") or entities.get("date"))
            )
            amount = entities.get("amount")
            amount_str = f" ({_money(amount)})" if isinstance(amount, (int, float)) else ""
            return (
                f"📅 *Promise recorded*\n"
                f"{debtor} will pay{amount_str} by {due_display}.\n"
                f"I'll check with you if it's not settled.\n"
                f"_(confidence: {conf}%)_"
            )

        elif etype == EventType.DEBT_CREATED:
            debtor = _customer_label(event, "debtor")
            due_display = _resolve_due_date(entities.get("due_date") or entities.get("date"))
            reply = f"📒 *Debt recorded*\n{debtor} owes {_money(entities.get('amount'))}."
            if due_display != "soon":
                reply += f" Due {due_display}."
            return reply + f"\n_(confidence: {conf}%)_"

        elif etype == EventType.EXPENSE_RECORDED:
            item = entities.get("item", "items")
            return f"💸 *Expense recorded*\n{_money(entities.get('amount'))} for {item}.\n_(confidence: {conf}%)_"

        elif etype == EventType.LOW_STOCK_ALERT:
            item = entities.get("item", "stock")
            qty = entities.get("quantity", "low")
            return f"⚠️ *Low stock alert*\n{item} is running low ({qty} remaining). I'll include it in your morning briefing."

        elif etype == EventType.INVENTORY_UPDATE:
            return f"📦 *Inventory updated*\n_(confidence: {conf}%)_"

        elif etype in (EventType.CUSTOMER_ORDER, EventType.ORDER_RECEIVED):
            who = _customer_label(event, "buyer")
            return f"🛍️ *Order noted*\nI've recorded the order for {who}.\n_(confidence: {conf}%)_"

        elif etype in (EventType.FOLLOW_UP_REQUIRED, EventType.REMINDER_REQUEST, EventType.APPOINTMENT_REQUEST, EventType.COMPLAINT) \
                and result.followup_scheduled:
            due_display = entities.get("_due_display") or "soon"
            return f"⏰ *Reminder set*\nI'll remind you {due_display if due_display != 'soon' else 'tomorrow'}.\n_(confidence: {conf}%)_"

        elif etype == EventType.UNKNOWN:
            return (
                "🤔 I wasn't sure how to classify that message. "
                "Could you rephrase? e.g. \"Brian paid 50k\" or \"Only 2 bags cement left\"."
            )

        else:
            summary = event.to_summary()
            return f"✅ *Recorded*\n{summary}"
