"""
SenteFlow AI — Entry Point
===========================
WhatsApp-native AI business assistant for SMEs.

Architecture:
  WhatsApp → Evolution API → /api/webhooks/whatsapp
    → normalize (webhook_handler) → queue (Firestore)
    → which business owns this number? (services/tenancy.py)
    → that business's MessageRouter
        → ContextEngine → EventExtractor (multi-provider LLM)
        → customer resolution → ActionDispatcher → reply

  Background tasks (started on app startup):
    → reminder_loop        (hourly — due payment promises / follow-ups, every business)
    → daily_briefing_loop  (each business once a day after BRIEFING_HOUR local time)
    → webhook recovery     (every 5 min — re-runs messages a restart interrupted)
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

# Load .env BEFORE reading any configuration. Sentry used to be initialised
# above this line, so SENTRY_DSN from .env was never seen.
load_dotenv()

import sentry_sdk

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.1)

import firebase_admin
from firebase_admin import credentials, firestore

from bootstrap.app_factory import create_app
from bootstrap.dependency_injection import build_dependencies
from core.errors import StructuredLogger

APP_VERSION = "7.0.0"

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

WEBHOOK_RECOVERY_INTERVAL_S = 5 * 60


# ── Firebase ──────────────────────────────────────────────────────────────────

def init_firebase():
    """
    Initialise the Firebase Admin SDK and return a Firestore client.

    A function, not module-level code, so importing `main` (as tests do) does
    not need live cloud credentials.
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
    _register_lifecycle(app)
    logger.info("senteflow_started", org_id=org_id, version=APP_VERSION)
    return app


# ── Background agents ─────────────────────────────────────────────────────────

# Long-lived loops need a strong reference, or the event loop may collect them.
_agent_tasks: set[asyncio.Task] = set()


def _spawn_agent(coro, name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _agent_tasks.add(task)
    task.add_done_callback(_agent_tasks.discard)
    logger.info(f"{name}_started")


async def _webhook_recovery_loop(interval_s: int = WEBHOOK_RECOVERY_INTERVAL_S) -> None:
    from api.routes.whatsapp import requeue_and_process_stale
    while True:
        try:
            await requeue_and_process_stale()
        except Exception as exc:
            logger.error("webhook_recovery_error", error=str(exc))
        await asyncio.sleep(interval_s)


async def start_background_agents(app_deps) -> None:
    """Start the proactive loops. Skips WhatsApp-dependent ones if Evolution isn't configured."""
    _spawn_agent(_webhook_recovery_loop(), name="webhook_recovery_loop")

    if app_deps is None or app_deps.wa_client is None:
        logger.info("proactive_agents_skipped", reason="no WhatsApp client configured")
        return

    from tasks.reminder_sender import reminder_loop
    from tasks.briefing import daily_briefing_loop

    _spawn_agent(
        reminder_loop(
            wa_client=app_deps.wa_client,
            repo=app_deps.repo,
            org_id=app_deps.org_id,
            tenants=app_deps.tenants,
        ),
        name="reminder_loop",
    )
    _spawn_agent(
        daily_briefing_loop(app_deps.wa_client, app_deps.repo, app_deps.tenants),
        name="daily_briefing_loop",
    )


async def stop_background_agents() -> None:
    for task in list(_agent_tasks):
        task.cancel()
    if _agent_tasks:
        await asyncio.gather(*_agent_tasks, return_exceptions=True)


def _register_lifecycle(fastapi_app) -> None:
    from contextlib import asynccontextmanager

    previous = fastapi_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_):
        async with previous(app_):
            await start_background_agents(deps)
            try:
                yield
            finally:
                await stop_background_agents()

    fastapi_app.router.lifespan_context = lifespan


def __getattr__(name: str):
    """
    Build the app on first access to `main.app`.

    `uvicorn main:app` imports this module and then reads the `app` attribute,
    so the ASGI entry point still works — but merely importing `main` no
    longer requires live Firebase credentials.
    """
    if name == "app":
        return build()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
