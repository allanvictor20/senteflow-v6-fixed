"""
SenteFlow AI — Reminder Sender
================================
Sends the business owner WhatsApp nudges about payment promises and
follow-ups they asked SenteFlow to track.

HOW IT WORKS
------------
1. Reads organizations/{org_id}/reminders where status == "pending".
2. A reminder is due when:
     - it has `due_at` (set from the promised date): due_at <= now
       (payment promises fire the morning AFTER the promised date;
       "remind me Friday…" fires Friday morning), or
     - it has no date: created_at + OVERDUE_HOURS <= now.
3. Sends the nudge to the owner who recorded it (sender_id).
4. Marks it "notified" ONLY if the send actually succeeded (the Evolution
   client reports failures as a return value, not an exception).
5. Remembers which reminders were just sent, so an owner reply of "YES"
   marks them settled (and records the payment if the amount is known).

Triggered by reminder_loop() (started in main.py, every POLL_INTERVAL_SECONDS
per business) and piggybacked on owner messages (message_router).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
    from repositories.transaction_repository import TransactionRepository
from utils.clock import utc_now

logger = logging.getLogger(__name__)

# How old an undated reminder must be before we send a nudge
OVERDUE_HOURS: int = 24

# How often the background loop checks for due reminders (seconds)
POLL_INTERVAL_SECONDS: int = 3600  # 1 hour

# One send at a time per process, so the hourly loop and the per-message
# piggyback can't both pick up the same reminder and send it twice.
_send_lock = asyncio.Lock()


def _parse_ts(value) -> Optional[datetime]:
    """Parse a stored timestamp to naive UTC. None if unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        from utils.clock import to_naive_utc
        dt = to_naive_utc(dt)
    return dt


def _format_reminder_message(reminder: dict, outstanding: Optional[float] = None) -> str:
    """Build a friendly WhatsApp nudge from a reminder document."""
    debtor = reminder.get("debtor") or "Someone"
    amount = reminder.get("amount")
    currency = reminder.get("currency", "UGX")
    due_display = reminder.get("due_date_display") or "soon"

    if reminder.get("kind") == "follow_up":
        note = (reminder.get("raw_message") or "").strip()
        return (
            f"⏰ *Reminder*\n"
            f"Follow up with {debtor}"
            + (f" — \"{note[:120]}\"" if note else "")
            + "\nReply YES when it's done."
        )

    amount_str = ""
    if isinstance(amount, (int, float)) and amount:
        amount_str = f" {currency} {amount:,.0f}"

    still_owes = ""
    if outstanding is not None and outstanding > 0 and (not amount or abs(outstanding - float(amount)) > 0.5):
        still_owes = f" They still owe {currency} {outstanding:,.0f}."

    return (
        f"⏰ *Payment reminder*\n"
        f"{debtor} promised to pay{amount_str} by {due_display}.{still_owes}\n"
        f"Has this been settled? Reply YES to mark it paid, or tell me what happened "
        f"(e.g. \"{debtor} paid 20k\")."
    )


def _ledger_balances(repo, org_id: str) -> dict:
    """customer_id/name → {owed, paid, balance} from the ledger. Empty on error."""
    try:
        from repositories.transaction_repository import SUMMARY_EVENT_LIMIT
        from services.responses.reply_generator import compute_customer_balances
        txns = repo.list_transactions(org_id, limit=SUMMARY_EVENT_LIMIT)
        if not isinstance(txns, list):
            return {}
        return compute_customer_balances(txns)
    except Exception as exc:
        logger.warning("reminder_balance_lookup_failed", extra={"error": str(exc)})
        return {}


def _outstanding_for(balances: dict, reminder: dict) -> Optional[float]:
    key = reminder.get("customer_id") or str(reminder.get("debtor") or "").strip().lower()
    entry = balances.get(key) if key else None
    if not entry or entry.get("owed", 0) <= 0:
        return None  # no recorded debt (a promise only)
    return float(entry["balance"])


def _is_due(reminder: dict, overdue_hours: int) -> Optional[bool]:
    """True/False, or None when the timestamps can't be read."""
    now = utc_now()
    due_at = _parse_ts(reminder.get("due_at"))
    if reminder.get("due_at") and due_at is None:
        return None
    if due_at is not None:
        return due_at <= now
    created = _parse_ts(reminder.get("created_at"))
    if created is None:
        return None
    return created <= now - timedelta(hours=overdue_hours)


async def send_overdue_reminders(
    wa_client: "EvolutionClient",
    repo: "TransactionRepository",
    org_id: str,
    overdue_hours: int = OVERDUE_HOURS,
) -> int:
    """
    Send every due reminder for one business. Returns the number sent.
    """
    if wa_client is None:
        return 0
    async with _send_lock:
        return await _send_due(wa_client, repo, org_id, overdue_hours)


async def _send_due(wa_client, repo, org_id: str, overdue_hours: int) -> int:
    from integrations.whatsapp.client import send_succeeded

    sent = 0
    try:
        docs = await asyncio.to_thread(
            lambda: (
                repo._db.collection("organizations").document(org_id)
                .collection("reminders")
                .where("status", "==", "pending")
                .get()
            )
        )
    except Exception as exc:
        logger.error("reminder_fetch_failed", extra={"error": str(exc), "org_id": org_id})
        return 0

    sent_by_owner: dict[str, list[str]] = {}
    balances: Optional[dict] = None

    for doc in docs:
        reminder = doc.to_dict() or {}

        due = _is_due(reminder, overdue_hours)
        if due is None:
            logger.warning(
                "reminder_bad_timestamp",
                extra={"doc_id": doc.id, "created_at": reminder.get("created_at"), "due_at": reminder.get("due_at")},
            )
            continue
        if not due:
            continue

        sender_id = reminder.get("sender_id")
        if not sender_id:
            logger.warning("reminder_missing_sender_id", extra={"doc_id": doc.id})
            continue

        if balances is None:
            balances = await asyncio.to_thread(_ledger_balances, repo, org_id)
        outstanding = _outstanding_for(balances, reminder)
        if outstanding is not None and outstanding <= 0.5 and reminder.get("kind", "payment") == "payment":
            # Already paid in full according to the ledger — close it quietly.
            try:
                await asyncio.to_thread(doc.reference.update, {
                    "status": "resolved",
                    "resolved_at": utc_now().isoformat(),
                    "resolved_by": "ledger",
                })
            except Exception as exc:
                logger.warning("reminder_autoresolve_failed", extra={"doc_id": doc.id, "error": str(exc)})
            continue

        message = _format_reminder_message(reminder, outstanding)

        try:
            result = await wa_client.send_text(sender_id, message)
        except Exception as exc:
            logger.error("reminder_send_failed", extra={"doc_id": doc.id, "error": str(exc)})
            continue
        if not send_succeeded(result):
            # Leave it pending — it will be retried on the next pass.
            logger.error("reminder_send_failed", extra={"doc_id": doc.id, "error": str(result.get("error"))})
            continue

        sent += 1
        sent_by_owner.setdefault(sender_id, []).append(doc.id)
        logger.info(
            "reminder_sent",
            extra={"doc_id": doc.id, "debtor": reminder.get("debtor"), "org_id": org_id},
        )

        try:
            await asyncio.to_thread(doc.reference.update, {
                "status": "notified",
                "notified_at": utc_now().isoformat(),
            })
        except Exception as exc:
            logger.warning("reminder_mark_failed", extra={"doc_id": doc.id, "error": str(exc)})

    # So a "YES" reply settles exactly the reminders the owner just saw.
    for owner, reminder_ids in sent_by_owner.items():
        try:
            await asyncio.to_thread(
                repo.upsert_conversation,
                org_id,
                owner,
                {
                    "pending_intent": "awaiting_reminder_ack",
                    "pending_context": {"reminder_ids": reminder_ids},
                    "pending_question": "reminder",
                },
            )
        except Exception as exc:
            logger.warning("reminder_ack_state_failed", extra={"error": str(exc)})

    if sent:
        logger.info("reminders_sent_total", extra={"count": sent, "org_id": org_id})

    return sent


async def mark_reminders_settled(repo, org_id: str, reminder_ids: list[str], owner_id: str) -> list[dict]:
    """
    The owner confirmed reminders are settled: close each reminder and its
    promise, and for payment reminders with a known amount record the payment
    in the ledger so balances stay right. Returns what was settled.
    """
    from domain.events.business_event import BusinessEvent
    from domain.events.event_types import EventType
    from services.actions.action_dispatcher import ActionDispatcher

    settled: list[dict] = []
    balances = await asyncio.to_thread(_ledger_balances, repo, org_id)
    reminders_col = repo._db.collection("organizations").document(org_id).collection("reminders")
    promises_col = repo._db.collection("organizations").document(org_id).collection("promises")

    for reminder_id in reminder_ids:
        try:
            snap = await asyncio.to_thread(reminders_col.document(reminder_id).get)
        except Exception as exc:
            logger.warning("reminder_lookup_failed", extra={"id": reminder_id, "error": str(exc)})
            continue
        if not snap.exists:
            continue
        reminder = snap.to_dict() or {}
        if reminder.get("status") == "resolved":
            continue

        now = utc_now().isoformat()
        update = {"status": "resolved", "resolved_at": now, "resolved_by": f"whatsapp:{owner_id.split('@')[0]}"}
        try:
            await asyncio.to_thread(snap.reference.update, update)
            promise = await asyncio.to_thread(promises_col.document(reminder_id).get)
            if promise.exists and (promise.to_dict() or {}).get("status") == "pending":
                await asyncio.to_thread(promise.reference.update, update)
        except Exception as exc:
            logger.warning("reminder_settle_failed", extra={"id": reminder_id, "error": str(exc)})
            continue

        amount = reminder.get("amount")
        # With a recorded debt, the payment is whatever is still outstanding
        # (part may already have been paid). With only a promise, it's the
        # promised amount.
        outstanding = _outstanding_for(balances, reminder)
        if outstanding is not None:
            amount = outstanding if outstanding > 0.5 else None
        if reminder.get("kind", "payment") == "payment" and isinstance(amount, (int, float)) and amount > 0:
            payment = BusinessEvent(
                event_type=EventType.PAYMENT_RECEIVED,
                raw_message=f"Settled via reminder: {reminder.get('raw_message') or ''}"[:200],
                sender_id=owner_id,
                business_id=org_id,
                confidence=1.0,
                customer_id=reminder.get("customer_id"),
                customer_name=reminder.get("debtor"),
                entities={
                    "amount": float(amount),
                    "currency": reminder.get("currency", "UGX"),
                    "payer": reminder.get("debtor"),
                    "customer": reminder.get("debtor"),
                    "customer_id": reminder.get("customer_id"),
                },
                # The owner's YES is the approval; promises are already closed above.
                recommended_actions=["update_ledger", "update_customer_profile"],
            )
            try:
                await ActionDispatcher(repo=repo, org_id=org_id).dispatch(
                    payment, approved_actions={"update_ledger"}
                )
            except Exception as exc:
                logger.warning("reminder_payment_record_failed", extra={"id": reminder_id, "error": str(exc)})

        settled.append({"debtor": reminder.get("debtor") or "Someone", "amount": amount if isinstance(amount, (int, float)) else None})
    return settled


async def reminder_loop(
    wa_client: "EvolutionClient",
    repo: "TransactionRepository",
    org_id: Optional[str] = None,
    poll_interval: int = POLL_INTERVAL_SECONDS,
    tenants=None,
) -> None:
    """
    Background loop: every poll_interval seconds, send due reminders for every
    business (all orgs known to `tenants`, or just `org_id`).

    This loop never raises — errors are logged and the loop continues.
    """
    logger.info("reminder_loop_started", extra={"interval_s": poll_interval})
    while True:
        try:
            org_ids = (await asyncio.to_thread(tenants.list_org_ids)) if tenants is not None else []
            if org_id and org_id not in org_ids:
                org_ids.append(org_id)
            for oid in org_ids:
                sent = await send_overdue_reminders(wa_client, repo, oid)
                if sent:
                    logger.info("reminder_loop_cycle", extra={"sent": sent, "org_id": oid})
        except Exception as exc:
            logger.error("reminder_loop_error", extra={"error": str(exc)})
        await asyncio.sleep(poll_interval)
