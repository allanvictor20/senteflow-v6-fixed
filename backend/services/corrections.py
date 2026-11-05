"""
SenteFlow AI — Corrections over WhatsApp
=========================================
The owner fixes mistakes in the same chat, in their own words:

  "undo" / "delete that" / "sazaamu"            → remove the last record
  "Brian paid 40k not 50k"                      → change the amount
  "it was Kato, not Brian"                      → change the customer
  "Brian will pay Monday not Friday"            → change the due date
  "that was an expense, not a payment"          → change the type

Flow (the router drives it; nothing changes without a YES):

  1. Find the record. The AI's hints (customer, amount, type) narrow the
     owner's recent records; with no hints it is the owner's last record.
     Nothing matches → numbered list of recent records, "reply with the number".
  2. Show it before/after and ask YES / NO.
  3. YES →
     - receipt / dashboard entries (ledger only): the entry is edited in
       place, or marked rejected for an undo; an audit trail is kept on it.
     - entries recorded from a WhatsApp message (a BusinessEvent): everything
       that message caused is reverted — ledger entry rejected, its promise /
       reminder / alert / order cancelled, promises it had closed reopened —
       and for a change, a corrected copy of the event is dispatched through
       the normal tools, so balances, reminders and closed promises come out
       exactly as if the owner had said it right the first time.
     - the customers' running totals are recomputed from the ledger.

Nothing is ever deleted: undone entries keep status "rejected" / "cancelled"
with who undid them and when, so the dashboard can still show the history.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from utils.clock import utc_now

logger = logging.getLogger(__name__)

# ── Record families ───────────────────────────────────────────────────────────

FAMILY_OF_EVENT = {
    "payment_received": "payment", "payment": "payment", "income": "payment",
    "debt_created": "debt",
    "expense_recorded": "expense",
    "payment_promise": "promise",
    "customer_order": "order", "order_received": "order",
    "low_stock_alert": "stock",
    "reminder_request": "reminder", "follow_up_required": "reminder",
}
FAMILY_OF_LEDGER = {
    "payment": "payment", "income": "payment",
    "expense": "expense",
    "debt_created": "debt",
    "payment_promise": "promise",
}
LEDGER_TYPE_OF_FAMILY = {"payment": "payment", "expense": "expense", "debt": "debt_created", "promise": "payment_promise"}
EVENT_TYPE_OF_FAMILY = {"payment": "payment_received", "debt": "debt_created", "expense": "expense_recorded", "promise": "payment_promise"}
FAMILY_ALIASES = {
    "payment": "payment", "payments": "payment", "paid": "payment", "payment_received": "payment",
    "income": "payment", "sale": "payment",
    "debt": "debt", "debts": "debt", "credit": "debt", "debt_created": "debt", "loan": "debt",
    "expense": "expense", "expenses": "expense", "expense_recorded": "expense", "spent": "expense",
    "promise": "promise", "payment_promise": "promise",
    "order": "order", "customer_order": "order", "order_received": "order",
    "stock": "stock", "low_stock_alert": "stock", "inventory": "stock",
    "reminder": "reminder",
}
LABEL = {
    "payment": "Payment", "debt": "Debt", "expense": "Expense", "promise": "Promise",
    "order": "Order", "stock": "Stock alert", "reminder": "Reminder",
}
# Role key that names the other party, per family.
ROLE_KEY = {"payment": "payer", "debt": "debtor", "promise": "debtor", "order": "buyer"}
_PERSON_KEYS = ("customer", "payer", "debtor", "buyer", "person")

RECENT_SCAN_LIMIT = 300
CHOICES_SHOWN = 5

_UNDO_RE = re.compile(
    r"^\s*(?:undo|sazaamu|delete|remove|scrap)"
    r"(?:\s+(?:that|it|this|last|the\s+last|my\s+last)(?:\s+(?:one|entry|record|message|transaction))?)?"
    r"\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_undo_command(text: str) -> bool:
    """"undo", "delete that", "remove the last one", "sazaamu" — no AI needed."""
    return bool(_UNDO_RE.match(text or ""))


# ── Records ──────────────────────────────────────────────────────────────────

@dataclass
class Record:
    id: str
    kind: str                 # "event" (recorded from a message) | "ledger" (receipt / dashboard)
    family: str
    amount: Optional[float] = None
    currency: str = "UGX"
    customer: str = ""
    customer_id: Optional[str] = None
    due_date: Optional[str] = None
    item: Optional[str] = None
    when: str = ""
    sender: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Record":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def record_from_doc(doc: dict) -> Optional[Record]:
    """A correctable Record from an events/ document, or None."""
    if doc.get("transaction_type"):
        # Ledger entries made by a WhatsApp message are corrected through that
        # message's BusinessEvent, so its side effects are reverted too.
        if doc.get("status") != "approved" or doc.get("source_event_id"):
            return None
        family = FAMILY_OF_LEDGER.get(doc["transaction_type"])
        if not family:
            return None
        return Record(
            id=doc.get("id") or doc.get("event_id") or "",
            kind="ledger",
            family=family,
            amount=_num(doc.get("amount")),
            currency=doc.get("currency") or "UGX",
            customer=_text(doc.get("customer_name")) or _text(doc.get("debtor")) or _text(doc.get("payer"))
            or _text(doc.get("payee")),
            customer_id=doc.get("customer_id"),
            due_date=doc.get("due_date"),
            item=_text(doc.get("description")) or None,
            when=str(doc.get("created_at") or doc.get("approved_at") or ""),
            sender=doc.get("sender_id") or doc.get("recorded_by") or "",
        )

    if "raw_message" in doc and doc.get("event_type"):
        if doc.get("processing_status") != "completed":
            return None
        family = FAMILY_OF_EVENT.get(doc["event_type"])
        if not family:
            return None
        ent = doc.get("entities") or {}
        return Record(
            id=doc.get("event_id") or doc.get("id") or "",
            kind="event",
            family=family,
            amount=_num(ent.get("amount")),
            currency=ent.get("currency") or "UGX",
            customer=_text(doc.get("customer_name")) or next((_text(ent.get(k)) for k in _PERSON_KEYS if _text(ent.get(k))), ""),
            customer_id=doc.get("customer_id"),
            due_date=ent.get("due_date"),
            item=_text(ent.get("item")) or None,
            when=str(doc.get("timestamp") or doc.get("created_at") or ""),
            sender=doc.get("sender_id") or "",
        )
    return None


# ── Describing records ───────────────────────────────────────────────────────

def _fmt_date(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")), lambda v: datetime.strptime(v[:10], "%Y-%m-%d")):
        try:
            return parse(s).strftime("%a %d %b")
        except ValueError:
            continue
    return s


def _money(amount: Optional[float], currency: str) -> str:
    return f"{currency} {amount:,.0f}" if amount is not None else "no amount"


def describe(r: Record, with_date: bool = True) -> str:
    who = r.customer or "someone"
    money = _money(r.amount, r.currency)
    due = _fmt_date(r.due_date)
    if r.family == "payment":
        text = f"{who} paid {money}"
    elif r.family == "debt":
        text = f"{who} owes {money}" + (f", due {due}" if due else "")
    elif r.family == "expense":
        text = money + (f" for {r.item}" if r.item else "") + (f" ({r.customer})" if r.customer else "")
    elif r.family == "promise":
        text = f"{who} will pay" + (f" {money}" if r.amount is not None else "") + (f" by {due}" if due else "")
    elif r.family == "order":
        text = f"order for {who}" + (f" ({r.item})" if r.item else "")
    elif r.family == "stock":
        text = f"{r.item or 'stock'} running low"
    else:
        text = f"follow up with {who}" + (f" by {due}" if due else "")
    when = _fmt_date(r.when) if with_date else ""
    return f"{LABEL.get(r.family, 'Record')} — {text}" + (f" · {when}" if when else "")


def apply_changes(r: Record, changes: dict) -> Record:
    updates: dict[str, Any] = {}
    if changes.get("amount") is not None:
        updates["amount"] = float(changes["amount"])
    if changes.get("customer"):
        updates["customer"] = changes["customer"]
        updates["customer_id"] = changes.get("customer_id")
    if changes.get("due_date"):
        updates["due_date"] = changes["due_date"]
    if changes.get("family"):
        updates["family"] = changes["family"]
    if changes.get("item"):
        updates["item"] = changes["item"]
    return replace(r, **updates)


# ── Understanding the owner's correction ─────────────────────────────────────

def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def name_matches(query: str, name: str) -> bool:
    """"Brian" matches "Brian Okello"; "okello" matches too."""
    q, n = _tokens(query), _tokens(name)
    return bool(q) and bool(n) and all(any(w == t or w.startswith(t) for w in n) for t in q)


def correction_action(entities: dict) -> str:
    raw = str((entities or {}).get("correction_action") or "").strip().lower()
    return "undo" if raw in ("undo", "delete", "remove", "cancel", "reverse") else "change"


def changes_from(entities: dict) -> dict:
    """New values the owner gave. Empty when they didn't say what's right."""
    entities = entities or {}
    changes: dict[str, Any] = {}
    amount = _num(entities.get("new_amount"))
    if amount is not None and amount > 0:
        changes["amount"] = amount
    customer = _text(entities.get("new_customer"))
    if customer and customer.lower() not in ("unknown", "someone", "none"):
        changes["customer"] = customer
    due = _text(entities.get("new_due_date"))
    if due:
        changes["due_date"] = due
    family = FAMILY_ALIASES.get(_text(entities.get("new_type")).lower())
    if family:
        changes["family"] = family
    item = _text(entities.get("new_item"))
    if item:
        changes["item"] = item
    return changes


def find_matches(records: list[Record], hints: dict, sender_id: str) -> list[Record]:
    """
    Records the owner may mean, newest first. With no hints, the owner's own
    last record (or the business's last one if they have none).
    """
    hints = hints or {}
    family = FAMILY_ALIASES.get(_text(hints.get("target_type")).lower())
    customer = _text(hints.get("target_customer"))
    amount = _num(hints.get("target_amount"))

    if not (family or customer or amount is not None):
        mine = [r for r in records if r.sender and r.sender == sender_id]
        return (mine or records)[:1]

    matches = list(records)
    if family:
        matches = [r for r in matches if r.family == family]
    if customer:
        matches = [r for r in matches if name_matches(customer, r.customer)]
    if amount is not None:
        exact = [r for r in matches if r.amount is not None and abs(r.amount - amount) < 0.5]
        if exact:
            matches = exact
        elif not (family or customer):
            matches = []   # the amount was the only clue and nothing has it
    return matches


def validate_change(r: Record, changes: dict) -> Optional[str]:
    """A question/explanation for the owner if the change can't be made as asked."""
    if changes.get("family") and r.family not in LEDGER_TYPE_OF_FAMILY:
        return "I can only switch a record between payment, debt, expense and promise."
    new = apply_changes(r, changes)
    if new == r:
        return f"That's already what I have:\n• {describe(r)}"
    if new.family in ("payment", "debt", "expense") and new.amount is None:
        return "How much was it? e.g. \"it was 40k\""
    if new.family in ("payment", "debt", "promise") and not new.customer:
        return "Who was it? e.g. \"it was Kato\""
    return None


def confirm_question(plan: dict) -> str:
    r = Record.from_dict(plan["target"])
    if plan["action"] == "undo":
        text = f"↩️ Remove this record?\n• {describe(r)}\n\nReply *YES* to remove it or *NO* to keep it."
    else:
        new = apply_changes(r, plan.get("changes") or {})
        text = (
            f"✏️ Fix this record?\n• Before: {describe(r)}\n• After: {describe(new, with_date=False)}\n\n"
            "Reply *YES* to save the fix or *NO* to leave it."
        )
    others = int(plan.get("other_matches") or 0)
    if others:
        text += f"\n_(This is the most recent match. {others} older one{'s' if others != 1 else ''} also match{'' if others != 1 else 'es'} — if you meant another, reply NO and tell me the date or amount.)_"
    return text


def choice_question(records: list[Record], intro: str) -> str:
    lines = [intro]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. {describe(r)}")
    lines.append("\nReply with the number, or *NO* to leave everything as it is.")
    return "\n".join(lines)


def _strip_footnote(reply: str) -> str:
    return re.sub(r"\n?_\(confidence: \d+%\)_\s*$", "", reply or "").strip()


# ── Service ──────────────────────────────────────────────────────────────────

DispatchFn = Callable[..., Awaitable[Any]]


class CorrectionService:
    def __init__(self, repo, org_id: str, dispatch: Optional[DispatchFn] = None):
        self.repo = repo
        self.org_id = org_id
        self._dispatch = dispatch

    # ── finding records ──────────────────────────────────────────────────────

    def recent_records(self) -> list[Record]:
        docs = self.repo.list_transactions(self.org_id, limit=RECENT_SCAN_LIMIT)
        records = [r for r in (record_from_doc(d) for d in docs) if r and r.id]
        records.sort(key=lambda r: r.when, reverse=True)
        return records

    def resolve_new_customer(self, name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """(customer_id or None if new, display name, question if ambiguous)."""
        from services.conversation.customer_resolution import CustomerResolver
        resolution = CustomerResolver(self.repo, self.org_id).resolve_name(name, create_if_new=False)
        if resolution.needs_clarification:
            question = resolution.clarification_question.split(" Reply with")[0]
            return None, None, f"{question} Tell me again with the full name, e.g. \"it was {name} Okello, not ...\"."
        return resolution.customer_id, resolution.display_name or name, None

    # ── applying ─────────────────────────────────────────────────────────────

    async def apply(self, plan: dict, by: str) -> tuple[str, set[str], Any]:
        """
        Apply a confirmed plan. Returns (reply, affected customer ids, new
        BusinessEvent or None).
        """
        r = Record.from_dict(plan["target"])
        if r.kind == "ledger":
            reply, affected = await asyncio.to_thread(self._apply_ledger, r, plan, by)
            new_event = None
        else:
            reply, affected, new_event = await self._apply_event(r, plan, by)
        affected = {c for c in affected if c}
        if affected:
            await asyncio.to_thread(self.refresh_customer_totals, affected)
        return reply, affected, new_event

    def _ensure_customer_id(self, changes: dict) -> Optional[str]:
        if not changes.get("customer") or changes.get("customer_id"):
            return changes.get("customer_id")
        try:
            created = self.repo.create_customer(self.org_id, display_name=changes["customer"])
            changes["customer_id"] = created["id"]
        except Exception as exc:
            logger.warning("correction_customer_create_failed", extra={"error": str(exc)})
        return changes.get("customer_id")

    def _apply_ledger(self, r: Record, plan: dict, by: str) -> tuple[str, set]:
        doc = self.repo.get_transaction(self.org_id, r.id)
        if not doc or doc.get("status") != "approved":
            return "ℹ️ That record was already changed or removed, so I left it alone.", set()
        ref = self.repo._db.collection("organizations").document(self.org_id).collection("events").document(r.id)
        now = utc_now().isoformat()

        if plan["action"] == "undo":
            ref.update({
                "status": "rejected",
                "rejected_by": by,
                "rejected_at": now,
                "rejection_reason": f"Undone over WhatsApp: {plan.get('note', '')}"[:300],
            })
            return f"↩️ *Removed*\n{describe(r)}", {r.customer_id}

        changes = dict(plan.get("changes") or {})
        new = apply_changes(r, changes)
        updates: dict[str, Any] = {}
        if new.amount != r.amount:
            updates["amount"] = new.amount
        if new.family != r.family:
            updates["transaction_type"] = LEDGER_TYPE_OF_FAMILY[new.family]
        if changes.get("customer"):
            customer_id = self._ensure_customer_id(changes)
            updates.update({"customer_name": changes["customer"], "customer_id": customer_id})
            new = replace(new, customer_id=customer_id)
        if changes.get("customer") or new.family != r.family:
            # Keep the role fields consistent with the (new) type and person.
            updates["payer"] = new.customer if new.family == "payment" else None
            updates["debtor"] = new.customer if new.family in ("debt", "promise") else None
        if changes.get("due_date"):
            updates["due_date"] = changes["due_date"]
        if changes.get("item"):
            updates["description"] = changes["item"]
        history = list(doc.get("corrections") or [])
        history.append({
            "at": now,
            "by": by,
            "note": plan.get("note", "")[:300],
            "before": {"amount": r.amount, "type": r.family, "customer": r.customer, "due_date": r.due_date, "item": r.item},
            "after": {"amount": new.amount, "type": new.family, "customer": new.customer, "due_date": new.due_date, "item": new.item},
        })
        updates["corrections"] = history
        updates["updated_at"] = now
        ref.update(updates)
        return f"✏️ *Fixed*\n{describe(new)}", {r.customer_id, new.customer_id}

    def _revert_event(self, doc: dict, marks: dict, by: str) -> list[dict]:
        """Undo everything one BusinessEvent did. Returns reopened commitments."""
        org = self.repo._db.collection("organizations").document(self.org_id)
        event_id = doc.get("event_id") or doc.get("id")
        now = utc_now().isoformat()

        ledger_ids = {doc.get("transaction_id")} if doc.get("transaction_id") else set()
        try:
            for d in org.collection("events").where("source_event_id", "==", event_id).get():
                ledger_ids.add(d.id)
        except Exception as exc:
            logger.warning("correction_ledger_lookup_failed", extra={"error": str(exc)})
        for txn_id in ledger_ids:
            try:
                org.collection("events").document(txn_id).update({
                    "status": "rejected",
                    "rejected_by": by,
                    "rejected_at": now,
                    "rejection_reason": "Corrected over WhatsApp",
                })
            except Exception as exc:
                logger.warning("correction_ledger_reject_failed", extra={"id": txn_id, "error": str(exc)})

        for collection in ("promises", "reminders", "alerts"):
            try:
                ref = org.collection(collection).document(event_id)
                if ref.get().exists:
                    ref.update({"status": "cancelled", "cancelled_at": now, "cancelled_by": by})
            except Exception as exc:
                logger.warning("correction_cancel_failed", extra={"collection": collection, "error": str(exc)})

        try:
            for d in org.collection("orders").where("source_event_id", "==", event_id).get():
                d.reference.update({"status": "cancelled", "updated_at": now})
        except Exception as exc:
            logger.warning("correction_order_cancel_failed", extra={"error": str(exc)})

        reopened: list[dict] = []
        reopener = getattr(self.repo, "reopen_commitments", None)
        if reopener is not None:
            try:
                reopened = reopener(self.org_id, event_id) or []
            except Exception as exc:
                logger.warning("correction_reopen_failed", extra={"error": str(exc)})

        org.collection("events").document(event_id).update({"processing_status": "cancelled", **marks})
        return reopened

    async def _apply_event(self, r: Record, plan: dict, by: str) -> tuple[str, set, Any]:
        from domain.events.business_event import BusinessEvent, ProcessingStatus
        from domain.events.event_types import EventType
        from services.actions.action_dispatcher import _TOOL_REGISTRY

        doc = await asyncio.to_thread(self.repo.get_event, self.org_id, r.id)
        if not doc or doc.get("processing_status") != "completed":
            return "ℹ️ That record was already changed or removed, so I left it alone.", set(), None
        now = utc_now().isoformat()
        note = plan.get("note", "")[:300]

        if plan["action"] == "undo":
            reopened = await asyncio.to_thread(
                self._revert_event, doc, {"undone_at": now, "undone_by": by, "undo_note": note}, by
            )
            reply = f"↩️ *Removed*\n{describe(r)}"
            if reopened:
                n = len({c.get("id") for c in reopened})
                reply += f"\nThe promise{'s' if n != 1 else ''} it had closed {'are' if n != 1 else 'is'} open again."
            return reply, {r.customer_id}, None

        # ── change: revert, then dispatch a corrected copy ────────────────────
        changes = dict(plan.get("changes") or {})
        old_event = BusinessEvent(**{k: v for k, v in doc.items() if k != "id"})
        entities = {
            k: v for k, v in (old_event.entities or {}).items()
            if not k.startswith("_") and k not in ("order_id",)
        }
        new_family = changes.get("family") or r.family
        event_type = EventType(EVENT_TYPE_OF_FAMILY[new_family]) if changes.get("family") else old_event.event_type

        if changes.get("amount") is not None:
            entities["amount"] = float(changes["amount"])
        if changes.get("item"):
            entities["item"] = changes["item"]
        if changes.get("due_date"):
            entities["due_date"] = changes["due_date"]

        customer_name = old_event.customer_name or r.customer or None
        customer_id = old_event.customer_id
        if changes.get("customer"):
            customer_id = await asyncio.to_thread(self._ensure_customer_id, changes)
            customer_name = changes["customer"]
            for key in _PERSON_KEYS:
                if key in entities:
                    entities[key] = customer_name
            entities["customer"] = customer_name
        if customer_name:
            role = ROLE_KEY.get(new_family)
            for key in ("payer", "debtor", "buyer"):
                if key != role and changes.get("family"):
                    entities.pop(key, None)
            if role:
                entities[role] = customer_name
            entities.setdefault("customer", customer_name)
        if customer_id:
            entities["customer_id"] = customer_id
        else:
            entities.pop("customer_id", None)

        new_event = old_event.model_copy(deep=True, update={
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "entities": entities,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "transaction_id": None,
            "processing_status": ProcessingStatus.PENDING,
            "recommended_actions": [],
            "confidence": 1.0,
            "reasoning": f"Correction of {r.id}: {note}",
            "context": {},
        })

        await asyncio.to_thread(
            self._revert_event, doc,
            {"corrected_by": new_event.event_id, "corrected_at": now, "correction_note": note}, by,
        )

        result = await self._dispatch(new_event, approved_actions=set(_TOOL_REGISTRY), skip_actions=())
        await asyncio.to_thread(self._link_correction, doc, new_event)

        new_record = apply_changes(r, changes)
        if not getattr(result, "success", False):
            return (
                "⚠️ I removed the old record but couldn't save the corrected one. "
                f"Please send it again:\n• {describe(new_record, with_date=False)}",
                {r.customer_id, customer_id},
                None,
            )
        reply = "✏️ *Fixed*\n" + _strip_footnote(getattr(result, "whatsapp_reply", "") or describe(new_record))
        return reply, {r.customer_id, customer_id}, new_event

    def _link_correction(self, old_doc: dict, new_event) -> None:
        """Point the new records at the ones they replace; keep the original date."""
        events = self.repo._db.collection("organizations").document(self.org_id).collection("events")
        try:
            events.document(new_event.event_id).update({"corrects": old_doc.get("event_id") or old_doc.get("id")})
        except Exception as exc:
            logger.warning("correction_link_failed", extra={"error": str(exc)})
        old_txn = old_doc.get("transaction_id")
        if new_event.transaction_id and old_txn:
            try:
                old = events.document(old_txn).get()
                created = (old.to_dict() or {}).get("created_at") if old.exists else None
                update = {"corrects": old_txn}
                if created:
                    update["created_at"] = created   # stays in its place in the history
                events.document(new_event.transaction_id).update(update)
            except Exception as exc:
                logger.warning("correction_ledger_link_failed", extra={"error": str(exc)})

    # ── keeping customer totals honest ───────────────────────────────────────

    def refresh_customer_totals(self, customer_ids: set[str]) -> None:
        """
        Customer memory and profiles keep running totals (total_paid,
        total_outstanding) that only ever add. After a correction they are
        recomputed from the ledger instead.
        """
        from repositories.transaction_repository import SUMMARY_EVENT_LIMIT
        from services.responses.reply_generator import compute_customer_balances

        try:
            balances = compute_customer_balances(self.repo.list_transactions(self.org_id, limit=SUMMARY_EVENT_LIMIT))
        except Exception as exc:
            logger.warning("correction_totals_failed", extra={"error": str(exc)})
            return
        org = self.repo._db.collection("organizations").document(self.org_id)
        for customer_id in customer_ids:
            entry = balances.get(customer_id) or {"paid": 0.0, "balance": 0.0}
            totals = {
                "total_paid": round(entry["paid"], 2),
                "total_outstanding": round(max(0.0, entry["balance"]), 2),
                "updated_at": utc_now().isoformat(),
            }
            for collection in ("memory", "customer_profiles"):
                try:
                    ref = org.collection(collection).document(customer_id)
                    if ref.get().exists:
                        ref.set(totals, merge=True)
                except Exception as exc:
                    logger.warning("correction_totals_write_failed", extra={"collection": collection, "error": str(exc)})


def new_plan(action: str, target: Record, changes: dict, note: str, other_matches: int = 0) -> dict:
    return {
        "action": action,
        "target": target.to_dict(),
        "changes": changes,
        "note": note,
        "other_matches": other_matches,
    }
