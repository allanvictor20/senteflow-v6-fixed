"""
SenteFlow AI — ActionDispatcher (v5)
======================================
Two key upgrades:

IDEA 01 — Business Tool System (P2):
  Actions are now typed ToolBase subclasses with required_entities validation.
  If required entities are missing, returns ToolResult(ask_clarification=True)
  which triggers a WhatsApp question instead of a broken confirmation.

  Before: "Unknown will pay soon — I'll remind you if it's not settled."
  After:  "Who should I remind, and by when? e.g. 'Remind Bruno to pay by Friday'"

IDEA 09 — Permission System (P2):
  dispatch() checks OrgConfig before executing each action.
  HIGH-RISK actions (reduce_debt, large update_ledger) ask owner for confirmation
  before executing. Owner trust is preserved.

  Before: clears debt automatically → owner: "Wait that was partial!"
  After:  "Should I clear this debt? Reply YES to confirm."

All v4 fixes retained: due-date resolver, structured reminder storage,
_build_reply covering all event types.
"""

import logging
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TYPE_CHECKING, Optional

from domain.events.business_event import BusinessEvent, EventResult, ProcessingStatus
from domain.events.event_types import EventType
from services.memory.memory_engine import BusinessMemoryEngine

if TYPE_CHECKING:
    from repositories.transaction_repository import TransactionRepository
    from integrations.whatsapp.client import EvolutionClient

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


# ─── Due-date resolver ────────────────────────────────────────────────────────

def _resolve_due_date(raw_due: Any) -> str:
    from datetime import datetime, timedelta
    if not raw_due:
        return "soon"
    s = str(raw_due).strip().lower()
    if s == "tomorrow":
        dt = datetime.utcnow() + timedelta(days=1)
        return dt.strftime("%a %d %b")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%a %d %b")
        except ValueError:
            pass
    return raw_due.strip().title()


# ─── Base tool class (IDEA 01) ────────────────────────────────────────────────

class ToolBase:
    """
    All actions are now typed tool classes with:
    - name: str
    - required_entities: list[str]  → missing = clarification question
    - optional_entities: list[str]
    - execute(): the actual implementation

    If required_entities are missing, execute() returns a ToolResult
    with ask_clarification=True and a specific question. Dispatcher
    sends the question to the owner instead of a broken confirmation.
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

    async def execute(
        self,
        event: BusinessEvent,
        repo: "TransactionRepository",
        org_id: str,
    ) -> ToolResult:
        raise NotImplementedError


# ─── Concrete tool implementations ───────────────────────────────────────────

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
            txn_type = {
                EventType.PAYMENT_RECEIVED: "payment",
                EventType.EXPENSE_RECORDED: "expense",
                EventType.DEBT_CREATED: "debt_created",
                EventType.PAYMENT_PROMISE: "payment_promise",
            }.get(event.event_type, "payment")

            from domain.models import Transaction
            txn = Transaction(
                amount=float(amount),
                currency=entities.get("currency", "UGX"),
                transaction_type=txn_type,
                category=entities.get("category", "other"),
                description=event.reasoning or event.raw_message[:100],
                payer=entities.get("payer") or entities.get("sender"),
                payee=entities.get("payee"),
                notes=f"[BusinessEvent:{event.event_id}]",
            )
            txn_id = await _maybe_await(repo.save_transaction(org_id, txn.model_dump(), event.sender_id))
            event.transaction_id = txn_id
            return ToolResult(outcome=f"update_ledger:ok:{txn_id}")
        except Exception as exc:
            logger.error("ledger_update_failed", extra={"error": str(exc)})
            return ToolResult(outcome=f"update_ledger:failed:{exc}")


class ReduceDebtTool(ToolBase):
    name = "reduce_debt"
    required_entities = []
    optional_entities = ["amount", "debtor"]

    async def execute(self, event, repo, org_id) -> ToolResult:
        logger.info("debt_reduction_queued", extra={"event_id": event.event_id})
        return ToolResult(outcome="reduce_debt:queued")


class ScheduleReminderTool(ToolBase):
    name = "schedule_reminder"
    required_entities = ["debtor"]
    optional_entities = ["due_date", "amount"]
    clarification_question = (
        "Who should I remind, and by when? "
        "e.g. 'Remind Bruno to pay by Friday'"
    )

    async def execute(self, event, repo, org_id) -> ToolResult:
        check = self._check_required(event)
        if check:
            return check

        from datetime import datetime
        entities = event.entities or {}
        due_raw = entities.get("due_date") or entities.get("date")
        due_display = _resolve_due_date(due_raw)
        amount = entities.get("amount")
        debtor = (
            entities.get("debtor")
            or entities.get("payer")
            or entities.get("customer")
            or "Unknown"
        )

        reminder_doc = {
            "type": "reminder",
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "due_date_raw": str(due_raw) if due_raw else None,
            "due_date_display": due_display,
            "amount": float(amount) if amount else None,
            "currency": entities.get("currency", "UGX"),
            "debtor": debtor,
            "sender_id": event.sender_id,
            "raw_message": event.raw_message,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
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

    async def execute(self, event, repo, org_id) -> ToolResult:
        entities = event.entities or {}
        customer_name = (
            entities.get("customer")
            or entities.get("payer")
            or entities.get("debtor")
            or entities.get("buyer")
        )
        updates: dict[str, Any] = {
            "display_name": customer_name,
            "last_event_type": event.event_type.value,
            "last_message": event.raw_message,
            "last_confidence": event.confidence,
        }
        if entities.get("amount"):
            updates["last_amount"] = entities["amount"]
        if entities.get("item"):
            updates["last_item"] = entities["item"]
        repo.upsert_customer_profile(org_id, event.sender_id, updates)
        return ToolResult(outcome="update_customer_profile:ok")


class UpdateConversationTool(ToolBase):
    name = "update_conversation"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        from services.conversation import ConversationStateManager, EntityLinker
        manager = ConversationStateManager(repo=repo, org_id=org_id)
        await _maybe_await(manager.apply_business_event(event))
        linker = EntityLinker(repo=repo, org_id=org_id)
        await _maybe_await(linker.link_business_event(event))
        return ToolResult(outcome="update_conversation:ok")


class CreateOrderTool(ToolBase):
    name = "create_order"
    required_entities = []
    optional_entities = ["customer", "buyer", "items", "item", "quantity", "amount"]

    async def execute(self, event, repo, org_id) -> ToolResult:
        entities = event.entities or {}
        order_id = repo.create_order(org_id, {
            "customer_id": event.sender_id,
            "customer_name": entities.get("customer") or entities.get("buyer"),
            "items": entities.get("items") or ([entities.get("item")] if entities.get("item") else []),
            "quantity": entities.get("quantity"),
            "amount": entities.get("amount"),
            "currency": entities.get("currency", "UGX"),
            "source_event_id": event.event_id,
            "source_message": event.raw_message,
            "status": "pending",
            "payment_status": "unpaid",
            "delivery_status": "pending",
        })
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
    name = "update_inventory"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        logger.info("inventory_update_queued", extra={"event_id": event.event_id})
        return ToolResult(outcome="update_inventory:queued")


class NotifyOwnerTool(ToolBase):
    name = "notify_owner"
    required_entities = []

    async def execute(self, event, repo, org_id) -> ToolResult:
        logger.info("owner_notification_queued", extra={"event_id": event.event_id, "type": event.event_type.value})
        return ToolResult(outcome="notify_owner:queued")


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
    EventType.EXPENSE_RECORDED: ["update_ledger", "update_conversation"],
    EventType.LOW_STOCK_ALERT: ["create_alert", "notify_owner", "update_conversation"],
    EventType.INVENTORY_UPDATE: ["update_inventory", "update_conversation"],
    EventType.CUSTOMER_INQUIRY: ["notify_owner", "update_customer_profile", "update_conversation"],
    EventType.CUSTOMER_ORDER: ["create_order", "schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.ORDER_RECEIVED: ["create_order", "schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.NEGOTIATION: ["notify_owner", "update_customer_profile", "update_conversation"],
    EventType.COMPLAINT: ["notify_owner", "schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.APPOINTMENT_REQUEST: ["schedule_reminder", "update_customer_profile", "update_conversation"],
    EventType.SUPPLIER_MESSAGE: ["schedule_reminder", "update_conversation"],
    EventType.DELIVERY_UPDATE: ["update_conversation"],
    EventType.FOLLOW_UP_REQUIRED: ["schedule_reminder", "update_conversation"],
    EventType.REMINDER_REQUEST: ["schedule_reminder", "update_conversation"],
    EventType.BUSINESS_NOTE: ["update_conversation"],
    EventType.UNKNOWN: ["notify_owner", "update_conversation"],
    EventType.PAYMENT: ["update_ledger"],
    EventType.INCOME: ["update_ledger"],
}


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

    async def dispatch(self, event: BusinessEvent) -> EventResult:
        from domain.permissions.model import OrgConfig

        result = EventResult(
            event_id=event.event_id,
            success=False,
            actions_executed=[],
            actions_failed=[],
        )

        action_keys = list(event.recommended_actions or _DEFAULT_ACTIONS.get(event.event_type, []))
        for required in ("update_customer_profile", "update_conversation"):
            if required in _TOOL_REGISTRY and required not in action_keys:
                action_keys.append(required)

        if not action_keys:
            result.success = True
            result.whatsapp_reply = self._build_reply(event, result)
            return result

        org_config = self.org_config or OrgConfig.default(self.org_id)

        for key in action_keys:
            tool = _TOOL_REGISTRY.get(key)
            if tool is None:
                logger.warning("unknown_action_key", extra={"key": key})
                result.actions_failed.append(f"{key}:unknown")
                continue

            # IDEA 09: permission check before execution
            perm = org_config.get_permission(key)
            amount = event.entities.get("amount")
            if perm.requires_approval(amount):
                question = perm.get_approval_question(event.to_summary())
                result.actions_executed.append(f"{key}:pending_approval")
                result.whatsapp_reply = question
                result.pending_approval = True
                logger.info("action_pending_approval", extra={"action": key, "event_id": event.event_id})
                # Stop further actions — owner must confirm first
                event.processing_status = ProcessingStatus.PENDING
                await _maybe_await(
                    self.repo.save_business_event(self.org_id, event.model_dump(mode="json"))
                )
                return result

            try:
                tool_result = await tool.execute(event, self.repo, self.org_id)

                # IDEA 01: clarification needed — short-circuit with a question
                if tool_result.ask_clarification:
                    result.whatsapp_reply = tool_result.clarification_question
                    result.success = True  # Not a failure — just need more info
                    result.actions_executed.append(tool_result.outcome)
                    event.processing_status = ProcessingStatus.PENDING
                    await _maybe_await(
                        self.repo.save_business_event(self.org_id, event.model_dump(mode="json"))
                    )
                    return result

                result.actions_executed.append(tool_result.outcome)
                logger.debug("action_executed", extra={"action": key, "outcome": tool_result.outcome})
            except Exception as exc:
                logger.error("action_failed", extra={"action": key, "error": str(exc)})
                result.actions_failed.append(f"{key}:error:{exc}")

        result.success = len(result.actions_failed) == 0 or len(result.actions_executed) > 0
        result.followup_scheduled = any(
            a.startswith("schedule_reminder:ok") for a in result.actions_executed
        )
        result.whatsapp_reply = self._build_reply(event, result)

        event.processing_status = (
            ProcessingStatus.COMPLETED if result.success else ProcessingStatus.FAILED
        )

        try:
            await _maybe_await(self.repo.save_business_event(self.org_id, event.model_dump(mode="json")))
        except Exception as exc:
            logger.warning("business_event_persist_failed", extra={"error": str(exc)})

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

    def _build_reply(self, event: BusinessEvent, result: EventResult) -> str:
        etype = event.event_type
        entities = event.entities
        conf = int(event.confidence * 100)

        if etype == EventType.PAYMENT_RECEIVED:
            payer = entities.get("payer", "Someone")
            amount = entities.get("amount", "?")
            currency = entities.get("currency", "UGX")
            amount_str = f"{currency} {amount:,.0f}" if isinstance(amount, (int, float)) else str(amount)
            return f"✅ *Payment recorded*\n{payer} paid {amount_str}\n_(confidence: {conf}%)_"

        elif etype == EventType.PAYMENT_PROMISE:
            debtor = entities.get("debtor", entities.get("payer", "Someone"))
            due_display = (
                entities.get("_due_display")
                or _resolve_due_date(entities.get("due_date") or entities.get("date"))
            )
            amount = entities.get("amount")
            amount_str = f" ({entities.get('currency', 'UGX')} {amount:,.0f})" if isinstance(amount, (int, float)) else ""
            return (
                f"📅 *Promise recorded*\n"
                f"{debtor} will pay{amount_str} by {due_display}.\n"
                f"I'll remind you if it's not settled.\n"
                f"_(confidence: {conf}%)_"
            )

        elif etype == EventType.DEBT_CREATED:
            amount = entities.get("amount")
            currency = entities.get("currency", "UGX")
            debtor = entities.get("debtor") or entities.get("customer") or "Someone"
            amount_str = f"{currency} {amount:,.0f}" if isinstance(amount, (int, float)) else (str(amount) if amount else "?")
            due_display = _resolve_due_date(entities.get("due_date") or entities.get("date"))
            reply = f"📒 *Debt recorded*\n{debtor} owes {amount_str}."
            if due_display != "soon":
                reply += f" Due {due_display}."
            return reply + f"\n_(confidence: {conf}%)_"

        elif etype == EventType.EXPENSE_RECORDED:
            amount = entities.get("amount")
            currency = entities.get("currency", "UGX")
            item = entities.get("item", "items")
            amount_str = f"{currency} {amount:,.0f}" if isinstance(amount, (int, float)) else (str(amount) if amount else "?")
            return f"💸 *Expense recorded*\n{amount_str} for {item}.\n_(confidence: {conf}%)_"

        elif etype == EventType.LOW_STOCK_ALERT:
            item = entities.get("item", "stock")
            qty = entities.get("quantity", "low")
            return f"⚠️ *Low stock alert*\n{item} is running low ({qty} remaining). Check your dashboard."

        elif etype == EventType.INVENTORY_UPDATE:
            return f"📦 *Inventory updated*\n_(confidence: {conf}%)_"

        elif etype == EventType.CUSTOMER_ORDER:
            return f"🛍️ *Order noted*\nI've recorded this customer order.\n_(confidence: {conf}%)_"

        elif etype == EventType.UNKNOWN:
            return (
                "🤔 I wasn't sure how to classify that message. "
                "Could you rephrase? e.g. \"Brian paid 50k\" or \"Only 2 bags cement left\"."
            )

        else:
            summary = event.to_summary()
            return f"✅ *Recorded*\n{summary}"
