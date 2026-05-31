"""
SenteFlow AI — Reminder Sender
================================
Checks Firestore for overdue payment promises and sends WhatsApp nudges.

This is the missing piece that makes schedule_reminder actually do something.

HOW IT WORKS
------------
1. Queries organizations/{org_id}/reminders where status == "pending"
2. For each reminder older than OVERDUE_HOURS (default 24h), sends a WhatsApp
   message back to the business owner (sender_id) who recorded it
3. Marks the reminder as "notified" so it is not sent again

HOW TO TRIGGER IT
-----------------
Option A — Manual (demo / testing):
    from tasks.reminder_sender import send_overdue_reminders
    await send_overdue_reminders(wa_client, repo, org_id)

Option B — On every incoming message (cheap, no scheduler needed):
    Add this to MessageRouter._route_via_event_pipeline after the reply is sent:
        asyncio.create_task(
            send_overdue_reminders(self.wa_client, self.repo, self.org_id)
        )
    This piggybacks on real activity — reminders fire when someone texts the bot,
    not on a clock. Good enough for an MVP/demo. Upgrade to APScheduler or Cloud
    Tasks when you need true scheduled delivery.

Option C — FastAPI startup background loop (lightweight):
    Add to main.py:
        @app.on_event("startup")
        async def _start_reminder_loop():
            import asyncio
            from tasks.reminder_sender import reminder_loop
            asyncio.create_task(reminder_loop(wa_client, repo, org_id))

    reminder_loop() runs every POLL_INTERVAL_SECONDS (default 3600 = 1 hour).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
    from repositories.transaction_repository import TransactionRepository

logger = logging.getLogger(__name__)

# How old a reminder must be before we send a nudge
OVERDUE_HOURS: int = 24

# How often the background loop checks for overdue reminders (seconds)
POLL_INTERVAL_SECONDS: int = 3600  # 1 hour


def _format_reminder_message(reminder: dict) -> str:
    """Build a friendly WhatsApp nudge from a reminder document."""
    debtor = reminder.get("debtor") or "Someone"
    amount = reminder.get("amount")
    currency = reminder.get("currency", "UGX")
    due_display = reminder.get("due_date_display") or "soon"

    amount_str = ""
    if isinstance(amount, (int, float)) and amount:
        amount_str = f" {currency} {amount:,.0f}"

    return (
        f"⏰ *Payment reminder*\n"
        f"{debtor} promised to pay{amount_str} by {due_display}.\n"
        f"Has this been settled? Reply 'yes' to clear it, or ignore to be reminded again tomorrow."
    )


async def send_overdue_reminders(
    wa_client: "EvolutionClient",
    repo: "TransactionRepository",
    org_id: str,
    overdue_hours: int = OVERDUE_HOURS,
) -> int:
    """
    Find all pending reminders older than overdue_hours and send WhatsApp nudges.

    Returns the number of reminders sent.
    """
    sent = 0
    threshold_dt = datetime.utcnow() - timedelta(hours=overdue_hours)

    try:
        docs = (
            repo._db.collection("organizations").document(org_id)
            .collection("reminders")
            .where("status", "==", "pending")
            .get()
        )
    except Exception as exc:
        logger.error("reminder_fetch_failed", extra={"error": str(exc), "org_id": org_id})
        return 0

    for doc in docs:
        reminder = doc.to_dict()
        created_at = reminder.get("created_at", "")

        # Skip if not yet overdue.
        # Compare as datetime objects, not raw ISO strings, because Firestore
        # may write timestamps with a trailing "Z" while Python's isoformat()
        # produces "+00:00" or no suffix — making lexicographic comparison
        # unreliable across formats.
        try:
            # Strip trailing Z and replace with +00:00 for fromisoformat compat
            created_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            # Normalise to naive UTC for comparison with threshold_dt (also naive UTC)
            if created_dt.tzinfo is not None:
                created_dt = created_dt.replace(tzinfo=None)
            if created_dt > threshold_dt:
                continue
        except (ValueError, TypeError):
            # Unparseable timestamp — skip this reminder to avoid false fires
            logger.warning("reminder_bad_created_at", extra={"doc_id": doc.id, "created_at": created_at})
            continue

        sender_id = reminder.get("sender_id")
        if not sender_id:
            logger.warning("reminder_missing_sender_id", extra={"doc_id": doc.id})
            continue

        message = _format_reminder_message(reminder)

        try:
            await wa_client.send_text(sender_id, message)
            sent += 1
            logger.info(
                "reminder_sent",
                extra={
                    "doc_id": doc.id,
                    "sender_id": sender_id,
                    "debtor": reminder.get("debtor"),
                    "org_id": org_id,
                },
            )
        except Exception as exc:
            logger.error(
                "reminder_send_failed",
                extra={"doc_id": doc.id, "error": str(exc)},
            )
            continue

        # Mark as notified so we don't spam
        try:
            doc.reference.update({
                "status": "notified",
                "notified_at": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.warning("reminder_mark_failed", extra={"doc_id": doc.id, "error": str(exc)})

    if sent:
        logger.info("reminders_sent_total", extra={"count": sent, "org_id": org_id})

    return sent


async def reminder_loop(
    wa_client: "EvolutionClient",
    repo: "TransactionRepository",
    org_id: str,
    poll_interval: int = POLL_INTERVAL_SECONDS,
) -> None:
    """
    Option C background loop: checks for overdue reminders every poll_interval seconds.
    Designed to run as an asyncio task from main.py on_event("startup").

    This loop never raises — errors are logged and the loop continues.
    """
    logger.info("reminder_loop_started", extra={"interval_s": poll_interval, "org_id": org_id})
    while True:
        try:
            sent = await send_overdue_reminders(wa_client, repo, org_id)
            if sent:
                logger.info("reminder_loop_cycle", extra={"sent": sent})
        except Exception as exc:
            logger.error("reminder_loop_error", extra={"error": str(exc)})
        await asyncio.sleep(poll_interval)
