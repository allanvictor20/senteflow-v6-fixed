"""
SenteFlow AI — Morning briefing
================================
Sends each business owner a short WhatsApp summary once a day:
overdue promises, stock alerts, customers who have gone quiet, revenue trend.

Scheduling:
  - Sent at BRIEFING_HOUR (default 7) in BUSINESS_TZ (default Africa/Kampala).
  - Exactly once per business per day, across restarts and multiple server
    instances: before sending, the sender atomically creates
    organizations/{org}/meta/briefing_{YYYY-MM-DD}. Whoever creates it sends;
    everyone else skips. If the send fails, the marker is removed so the next
    check retries.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from utils.clock import local_now, utc_now

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 15 * 60


def briefing_hour() -> int:
    try:
        return max(0, min(23, int(os.environ.get("BRIEFING_HOUR", "7"))))
    except ValueError:
        return 7


def build_briefing(repo, org_id: str, today: Optional[datetime] = None) -> Optional[str]:
    """Compose the briefing text for one business, or None if there's nothing to say."""
    from repositories.transaction_repository import SUMMARY_EVENT_LIMIT
    from services.memory import operational_intelligence as oi

    def _safe(fetch, label):
        try:
            return fetch() or []
        except Exception as exc:
            logger.warning(f"briefing_fetch_{label}_failed", extra={"error": str(exc), "org_id": org_id})
            return []

    events = _safe(lambda: repo.list_transactions(org_id, limit=SUMMARY_EVENT_LIMIT), "events")
    customers = _safe(lambda: repo.list_customers(org_id, limit=500), "customers") if hasattr(repo, "list_customers") else []
    promises = _safe(lambda: repo.list_open_promises(org_id), "promises") if hasattr(repo, "list_open_promises") else []

    lines: list[str] = []

    try:
        overdue = oi.detect_overdue_debts(promises or events)
        if overdue:
            lines.append(f"💰 *Overdue payments*: {len(overdue)} past the promised date")
            for d in overdue[:3]:
                name = d.get("debtor") or d.get("customer") or "Unknown"
                amt = d.get("amount")
                try:
                    amt_str = f" — UGX {float(amt):,.0f}" if amt else ""
                except (TypeError, ValueError):
                    amt_str = ""
                lines.append(f"  • {name}{amt_str}")
    except Exception as exc:
        logger.warning("briefing_overdue_debts_failed", extra={"error": str(exc)})

    try:
        inv_risks = oi.detect_inventory_risk(events)
        if inv_risks:
            items = ", ".join(str(r.get("item")) for r in inv_risks[:3] if r.get("item"))
            lines.append(f"📦 *Stock alerts*: {len(inv_risks)} item(s) running low" + (f" ({items})" if items else ""))
    except Exception as exc:
        logger.warning("briefing_inventory_failed", extra={"error": str(exc)})

    try:
        lost = oi.detect_lost_customers(customers)
        if lost:
            lines.append(f"👤 *Quiet customers*: {len(lost)} not seen in 45+ days")
    except Exception as exc:
        logger.warning("briefing_lost_customers_failed", extra={"error": str(exc)})

    try:
        trend = oi.detect_revenue_trends(events)
        direction = (trend or {}).get("trend", "")
        if direction and direction != "stable":
            emoji = "📈" if direction == "up" else "📉"
            change_pct = float(trend.get("change_percent", 0) or 0)
            lines.append(f"{emoji} *Revenue*: {'up' if direction == 'up' else 'down'} {abs(change_pct):.1f}% vs last week")
    except Exception as exc:
        logger.warning("briefing_revenue_failed", extra={"error": str(exc)})

    if not lines:
        return None
    day = (today or local_now()).strftime("%a %d %b")
    return f"Good morning! Here's your SenteFlow briefing for {day}:\n\n" + "\n".join(lines)


def _marker_ref(repo, org_id: str, day_key: str):
    return (
        repo._db.collection("organizations").document(org_id)
        .collection("meta").document(f"briefing_{day_key}")
    )


def claim_briefing(repo, org_id: str, day_key: str) -> bool:
    """Atomically claim today's briefing. True only for the first caller."""
    ref = _marker_ref(repo, org_id, day_key)
    try:
        ref.create({"claimed_at": utc_now().isoformat()})
        return True
    except Exception as exc:
        # AlreadyExists (or anything else) → someone else has it / don't risk a duplicate.
        logger.info("briefing_already_claimed", extra={"org_id": org_id, "day": day_key, "reason": type(exc).__name__})
        return False


def release_briefing(repo, org_id: str, day_key: str) -> None:
    try:
        _marker_ref(repo, org_id, day_key).delete()
    except Exception as exc:
        logger.warning("briefing_release_failed", extra={"error": str(exc)})


async def send_briefing_for_org(wa_client, repo, tenants, org_id: str, now=None) -> bool:
    """Send today's briefing for one business if it's time and not already sent."""
    from integrations.whatsapp.client import send_succeeded
    from services.tenancy import to_whatsapp_jid

    now = now or local_now()
    if now.hour < briefing_hour():
        return False

    owners = await asyncio.to_thread(tenants.owner_phones, org_id)
    if not owners:
        return False

    day_key = now.strftime("%Y-%m-%d")
    if not await asyncio.to_thread(claim_briefing, repo, org_id, day_key):
        return False

    message = await asyncio.to_thread(build_briefing, repo, org_id, now)
    if not message:
        logger.info("daily_briefing_nothing_to_report", extra={"org_id": org_id})
        return False  # keep the claim: nothing to say today either

    delivered = False
    for phone in owners:
        try:
            result = await wa_client.send_text(to_whatsapp_jid(phone), message)
            delivered = delivered or send_succeeded(result)
        except Exception as exc:
            logger.warning("daily_briefing_send_failed", extra={"org_id": org_id, "error": str(exc)})

    if not delivered:
        await asyncio.to_thread(release_briefing, repo, org_id, day_key)
        return False
    logger.info("daily_briefing_sent", extra={"org_id": org_id, "recipients": len(owners)})
    return True


async def daily_briefing_loop(wa_client, repo, tenants, check_every_s: int = CHECK_EVERY_SECONDS) -> None:
    """Check every few minutes; each business gets one briefing per day after BRIEFING_HOUR."""
    logger.info("daily_briefing_loop_running", extra={"hour": briefing_hour()})
    while True:
        try:
            org_ids = await asyncio.to_thread(tenants.list_org_ids)
            for org_id in org_ids:
                await send_briefing_for_org(wa_client, repo, tenants, org_id)
        except Exception as exc:
            logger.error("daily_briefing_loop_error", extra={"error": str(exc)})
        await asyncio.sleep(check_every_s)
