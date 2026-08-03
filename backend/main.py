"""
SenteFlow AI — v5 Entry Point
================================
WhatsApp-native AI business memory assistant for SMEs.

Changes from v4 (IDEA 07 — Proactive Agents, P3):
  reminder_loop() and daily_briefing_loop() are now started on app startup.
  The intelligence functions existed in v4 but were commented out ("Option C").
  Now wired in — owner gets morning briefings and overdue reminders without
  having to message in first.

Architecture:
  WhatsApp → Evolution API → /api/webhooks/whatsapp
    → normalize (webhook_handler)
    → route (message_router)
    → ProcessMessageWorkflow
        → ContextEngine (memory enrichment — parallel fetch, IDEA 10)
        → EventExtractor (multi-provider LLM, IDEA 06+08)
        → UpdateMemoryWorkflow (learn from event)
        → GenerateReplyWorkflow (craft reply)
    → WhatsApp reply

  Background tasks (IDEA 07):
    → reminder_loop        (every 1h — surfaces overdue payment promises)
    → daily_briefing_loop  (every 24h — morning summary to owner)
"""

import asyncio

import logging
import os

from dotenv import load_dotenv
import sentry_sdk

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.1)

import firebase_admin
from firebase_admin import credentials, firestore

from bootstrap.app_factory import create_app
from bootstrap.dependency_injection import build_dependencies
from core.errors import StructuredLogger
from utils.clock import utc_now

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = StructuredLogger(__name__)

org_id = os.environ.get("DEFAULT_ORG_ID", "default")

# Populated by build() — module-level so the background loops below can reach
# them once the app has been constructed. `app` is deliberately NOT predefined:
# the module __getattr__ at the bottom of this file builds it on first access.
db = None
deps = None


# ── Firebase ──────────────────────────────────────────────────────────────────

def init_firebase():
    """
    Initialise the Firebase Admin SDK and return a Firestore client.

    Deliberately a function, not module-level code: importing this module used
    to open a Firestore connection as a side effect, which made `main` (and so
    the helpers defined in it) unimportable without live cloud credentials.
    """
    if not firebase_admin._apps:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
    return firestore.client()


def build():
    """Construct the FastAPI app together with its dependency graph."""
    global db, deps, app
    db = init_firebase()
    deps = build_dependencies(db=db, org_id=org_id)
    app = create_app(deps=deps)
    _register_startup_hooks(app)
    logger.info("senteflow_started", org_id=org_id, version="6.0.0")
    return app


# ── IDEA 07: Proactive background agents ─────────────────────────────────────

# Long-lived loops need a strong reference, or the event loop may collect them.
_agent_tasks: set[asyncio.Task] = set()


def _spawn_agent(coro, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _agent_tasks.add(task)
    task.add_done_callback(_agent_tasks.discard)
    logger.info(f"{name}_started")


def _register_startup_hooks(fastapi_app) -> None:
    @fastapi_app.on_event("startup")
    async def start_proactive_agents():
        """
        IDEA 07: Wire up proactive loops that were built but never started.
        - reminder_loop: checks for overdue payment promises every hour
        - daily_briefing_loop: sends owner a morning summary every 24h

        Requires wa_client to be configured (EVOLUTION_API_URL + EVOLUTION_API_TOKEN).
        Gracefully skips if WhatsApp is not configured (dev/test environments).
        """
        if deps is None or deps.wa_client is None:
            logger.info("proactive_agents_skipped", reason="no WhatsApp client configured")
            return

        from tasks.reminder_sender import reminder_loop
        _spawn_agent(
            reminder_loop(wa_client=deps.wa_client, repo=deps.repo, org_id=org_id),
            name="reminder_loop",
        )
        _spawn_agent(
            _daily_briefing_loop(wa_client=deps.wa_client, repo=deps.repo, org_id=org_id),
            name="daily_briefing_loop",
        )


async def _daily_briefing_loop(wa_client, repo, org_id: str, interval_hours: int = 24) -> None:
    """
    IDEA 07: Send the owner a proactive morning briefing once per day.

    Calls operational_intelligence functions and synthesises a summary:
    - Overdue debts
    - Low stock alerts
    - Lost customers (not seen in 45 days)
    - Revenue trend

    Owner phone number is read from DEFAULT_OWNER_PHONE env var or the
    BusinessProfile. Skips silently if not configured.

    Design: run immediately on first loop iteration so the owner receives a
    briefing shortly after deploy, then sleep for interval_hours between
    subsequent runs.
    """
    import os
    from services.memory.operational_intelligence import (
        detect_overdue_debts,
        detect_lost_customers,
        detect_inventory_risk,
        detect_revenue_trends,
    )

    poll_seconds = interval_hours * 3600
    owner_phone = os.environ.get("DEFAULT_OWNER_PHONE")

    logger.info("daily_briefing_loop_running", interval_h=interval_hours)

    while True:
        # Run briefing first, then sleep — so the first briefing fires on startup
        # rather than after a full 24-hour wait.
        if not owner_phone:
            logger.warning("daily_briefing_skipped — DEFAULT_OWNER_PHONE not set")
        else:
            try:
                lines: list[str] = []

                # Fetch raw data from Firestore, then pass to the pure functions
                # in operational_intelligence.  Those functions accept list[dict],
                # not (repo, org_id) — fetch once here and reuse.
                try:
                    raw_events = repo.list_transactions(org_id, limit=500) or []
                except Exception as exc:
                    logger.warning("briefing_fetch_events_failed", error=str(exc))
                    raw_events = []

                try:
                    raw_customers = repo.list_customers(org_id, limit=500) if hasattr(repo, "list_customers") else []
                except Exception as exc:
                    logger.warning("briefing_fetch_customers_failed", error=str(exc))
                    raw_customers = []

                # Overdue payment promises
                try:
                    overdue = detect_overdue_debts(raw_events)
                    if overdue:
                        lines.append(f"💰 *Overdue payments*: {len(overdue)} customer(s) past due date")
                        for d in overdue[:3]:
                            name = d.get("debtor") or d.get("customer") or "Unknown"
                            amt = d.get("amount")
                            amt_str = f" — UGX {float(amt):,.0f}" if amt else ""
                            lines.append(f"  • {name}{amt_str}")
                except Exception as exc:
                    logger.warning("briefing_overdue_debts_failed", error=str(exc))

                # Inventory risks (uses event list)
                try:
                    inv_risks = detect_inventory_risk(raw_events)
                    if inv_risks:
                        lines.append(f"📦 *Stock alerts*: {len(inv_risks)} item(s) at risk")
                except Exception as exc:
                    logger.warning("briefing_inventory_failed", error=str(exc))

                # Lost customers (uses customer list, not event list)
                try:
                    lost = detect_lost_customers(raw_customers)
                    if lost:
                        lines.append(f"👤 *Lost customers*: {len(lost)} not seen in 45+ days")
                except Exception as exc:
                    logger.warning("briefing_lost_customers_failed", error=str(exc))

                # Revenue trend — key is "trend", not "direction"
                try:
                    trend = detect_revenue_trends(raw_events)
                    if trend:
                        direction = trend.get("trend", "")  # field is "trend", not "direction"
                        if direction and direction != "stable":
                            emoji = "📈" if direction == "up" else "📉"
                            change_pct = trend.get("change_percent", 0)
                            summary = f"{abs(change_pct):.1f}% vs last week"
                            lines.append(f"{emoji} *Revenue*: {summary}")
                except Exception as exc:
                    logger.warning("briefing_revenue_failed", error=str(exc))

                if lines:
                    from datetime import datetime
                    greeting = f"Good morning! Here's your SenteFlow briefing for {utc_now().strftime('%a %d %b')}:\n\n"
                    message = greeting + "\n".join(lines)
                    await wa_client.send_text(owner_phone, message)
                    logger.info("daily_briefing_sent", lines=len(lines))
                else:
                    logger.info("daily_briefing_nothing_to_report")

            except Exception as exc:
                logger.error("daily_briefing_loop_error", error=str(exc))

        await asyncio.sleep(poll_seconds)


def __getattr__(name: str):
    """
    Build the app on first access to `main.app`.

    `uvicorn main:app` imports this module and then reads the `app` attribute,
    so the ASGI entry point still works — but merely importing `main` (as the
    tests do, to reach `_daily_briefing_loop`) no longer requires live Firebase
    credentials.
    """
    if name == "app":
        return build()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
