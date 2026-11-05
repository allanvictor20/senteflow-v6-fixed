"""
SenteFlow AI — WhatsApp Webhook Routes
========================================
HTTP entry point for Evolution API webhook events.

POST /api/webhooks/whatsapp  — receives all incoming WhatsApp events

This route does NOTHING except:
  1. Validate the payload
  2. Normalize via webhook_handler
  3. Queue for background processing (so Evolution API gets a fast 200 OK)
  4. Return immediately

Background processing then looks up which business the sender's number
belongs to (services/tenancy.py) and hands the message to that business's
MessageRouter. Numbers that are not linked to any business get a one-line
"not registered" reply and no data.
"""

import asyncio
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from integrations.whatsapp.message_normalizer import normalize_message
from core.message_event import MessageEvent
from core.errors import StructuredLogger

logger = StructuredLogger(__name__)

whatsapp_router = APIRouter(prefix="/api/webhooks", tags=["whatsapp"])

# Set at app startup (see bootstrap/app_factory.py)
_deps = None

# Unknown numbers get at most one "not registered" reply per process per day,
# so a stranger cannot make the bot spam them (or us) in a loop.
_UNREGISTERED_REPLY_INTERVAL_S = 24 * 3600
_unregistered_replied: dict[str, float] = {}


def set_whatsapp_dependencies(deps=None, wa_client=None, message_router=None):
    """
    Wire the webhook to the app's dependencies.

    `deps` (AppDependencies) is the normal path. The `wa_client` /
    `message_router` keywords are kept so older callers and tests still work:
    a lone router is used for every sender, without tenancy checks.
    """
    global _deps
    if deps is not None:
        _deps = deps
        return
    _deps = _SingleRouterDeps(wa_client, message_router)


class _SingleRouterDeps:
    """Adapter for the legacy (wa_client, message_router) wiring."""

    def __init__(self, wa_client, message_router):
        self.wa_client = wa_client
        self._router = message_router
        self.repo = getattr(message_router, "repo", None)
        self.org_id = getattr(message_router, "org_id", "default")
        self.tenants = None

    def router_for(self, org_id):
        return self._router


def _queue_repo():
    return getattr(_deps, "repo", None) if _deps else None


def _queue_org() -> str:
    # The webhook queue is a system-level inbox; it lives under the default
    # org because the owning business is only known after the phone lookup.
    return getattr(_deps, "org_id", "default") if _deps else "default"


# ─── Authentication ───────────────────────────────────────────────────────────

def _authenticate_webhook(request: Request, raw_body: bytes, payload: dict) -> None:
    """
    Accept the request only if it proves it came from our Evolution instance.

    Two mechanisms are supported, in order of preference:
      1. HMAC-SHA256 over the raw body, keyed by WEBHOOK_SECRET
      2. A shared secret in the `apikey` header/body, matching the Evolution
         API key (EVOLUTION_API_TOKEN or EVOLUTION_API_KEY)

    In production at least one must be configured. Without that guard anyone
    who learns the URL can inject fabricated messages into the pipeline.
    """
    from integrations.whatsapp.webhook_handler import verify_webhook_signature

    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
    expected_key = os.environ.get("EVOLUTION_API_TOKEN") or os.environ.get("EVOLUTION_API_KEY", "")
    is_production = os.environ.get("ENVIRONMENT", "production").lower() in ("production", "prod")

    if webhook_secret:
        sig_header = request.headers.get("x-webhook-signature", "")
        if not verify_webhook_signature(raw_body, sig_header or None, webhook_secret):
            logger.warning("webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        return

    if expected_key:
        incoming_key = (
            request.headers.get("apikey")
            or request.headers.get("x-api-key")
            or payload.get("apikey", "")
        )
        if not hmac.compare_digest(str(incoming_key), expected_key):
            logger.warning(
                "webhook_unauthorized",
                incoming_key=(str(incoming_key)[:6] + "***") if incoming_key else "missing",
            )
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    if is_production:
        logger.error("webhook_auth_not_configured")
        raise HTTPException(
            status_code=503,
            detail="Webhook authentication is not configured",
        )

    logger.warning("webhook_auth_skipped_dev_mode")


def _strip_inline_media(payload: dict) -> dict:
    """
    Evolution can inline the whole media file as base64 in the webhook. We
    fetch media separately (decrypted) at processing time, and storing the
    base64 would push the queue document past Firestore's 1 MiB limit.
    """
    try:
        message = (payload.get("data") or {}).get("message") or {}
        if isinstance(message, dict) and "base64" in message:
            payload = json.loads(json.dumps(payload))
            payload["data"]["message"].pop("base64", None)
    except Exception:
        pass
    return payload


# ─── Webhook Endpoint ─────────────────────────────────────────────────────────

@whatsapp_router.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Evolution API posts all incoming WhatsApp events here.
    Must respond in < 5 seconds or Evolution API may retry.
    Heavy work (AI extraction) is queued as a background task.
    """
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    _authenticate_webhook(request, raw_body, payload)

    event = normalize_message(payload)
    if event is None:
        return JSONResponse({"status": "ignored"}, status_code=200)

    logger.info(
        "webhook_received",
        sender=event.display_sender,
        type=event.message_type.value,
        event_id=event.event_id,
    )

    repo = _queue_repo()
    if repo is None:
        logger.error("message_router_not_initialized")
        raise HTTPException(status_code=503, detail="WhatsApp router not initialised")

    try:
        # De-duplicate on the WhatsApp message id, not event.event_id: the
        # latter is a fresh uuid4 minted per normalisation, so an Evolution
        # retry of the same message would produce a different key every time
        # and be processed again.
        created, queue_id = await asyncio.to_thread(
            repo.enqueue_webhook_event,
            org_id=_queue_org(),
            event_id=event.message_id,
            payload=_strip_inline_media(payload),
            normalized_event=event.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.error("webhook_enqueue_failed", error=str(exc), event_id=event.event_id)
        raise HTTPException(status_code=503, detail="Could not queue webhook event")

    if not created:
        return JSONResponse({"status": "duplicate", "event_id": event.event_id}, status_code=200)

    background_tasks.add_task(process_queued_event, queue_id)
    return JSONResponse({"status": "queued", "event_id": event.event_id}, status_code=200)


async def process_queued_event(queue_id: str) -> None:
    """Claim a queued webhook event, route it to the sender's business, mark it done."""
    repo = _queue_repo()
    if repo is None:
        logger.error("message_router_not_initialized")
        return

    queue_org = _queue_org()
    claimed = await asyncio.to_thread(repo.mark_webhook_processing, queue_org, queue_id)
    if not claimed:
        return

    queued = await asyncio.to_thread(repo.get_queued_webhook_event, queue_org, queue_id)
    if not queued:
        logger.error("queued_event_missing", queue_id=queue_id)
        return

    try:
        event = MessageEvent(**queued["normalized_event"])
        router = await _router_for_sender(event)
        if router is not None:
            await router.route(event)
        await asyncio.to_thread(repo.mark_webhook_completed, queue_org, queue_id)
    except Exception as exc:
        logger.error("queued_event_failed", queue_id=queue_id, error=str(exc))
        try:
            await asyncio.to_thread(repo.mark_webhook_failed, queue_org, queue_id, str(exc))
        except Exception as mark_exc:
            logger.error("queued_event_mark_failed", queue_id=queue_id, error=str(mark_exc))


# Backward-compatible name
_process_queued_event = process_queued_event


async def _router_for_sender(event: MessageEvent):
    """Return the MessageRouter for the sender's business, or None if unregistered."""
    tenants = getattr(_deps, "tenants", None)
    if tenants is None:
        # Legacy single-router wiring (tests / old callers).
        return _deps.router_for(getattr(_deps, "org_id", "default"))

    org_id: Optional[str] = await asyncio.to_thread(tenants.org_for_phone, event.sender_id)
    if org_id:
        return _deps.router_for(org_id)

    logger.warning("whatsapp_sender_not_registered", sender=event.sender_id.split("@")[0][-4:])
    await _reply_unregistered(event)
    return None


async def _reply_unregistered(event: MessageEvent) -> None:
    wa_client = getattr(_deps, "wa_client", None)
    if wa_client is None:
        return
    now = time.monotonic()
    last = _unregistered_replied.get(event.sender_id)
    if last is not None and now - last < _UNREGISTERED_REPLY_INTERVAL_S:
        return
    _unregistered_replied[event.sender_id] = now
    try:
        await wa_client.send_text(
            event.chat_id or event.sender_id,
            "This WhatsApp number isn't linked to a SenteFlow business yet. "
            "If you're the owner, add it under Settings → WhatsApp numbers in your SenteFlow dashboard.",
        )
    except Exception as exc:
        logger.warning("unregistered_reply_failed", error=str(exc))


async def requeue_and_process_stale(stale_after_minutes: int = 10, max_attempts: int = 3) -> int:
    """
    Recover webhook events that never finished: items stuck in `processing`
    (the process restarted mid-message) and items that `failed` with attempts
    left. Called periodically from main.py.
    """
    repo = _queue_repo()
    if repo is None or not hasattr(repo, "requeue_stale_webhooks"):
        return 0
    queue_ids = await asyncio.to_thread(
        repo.requeue_stale_webhooks, _queue_org(), stale_after_minutes, max_attempts
    )
    for queue_id in queue_ids:
        await process_queued_event(queue_id)
    if queue_ids:
        logger.info("webhook_queue_recovered", count=len(queue_ids))
    return len(queue_ids)


# ─── Session Status Endpoint ──────────────────────────────────────────────────

@whatsapp_router.get("/whatsapp/status")
async def whatsapp_status():
    """Check Evolution API session health."""
    wa_client = getattr(_deps, "wa_client", None)
    if not wa_client:
        return JSONResponse({"status": "not_configured"}, status_code=503)

    status = await wa_client.get_session_status()
    health = await wa_client.health_check()

    return {
        "session": status,
        "health": health,
        "configured": True,
    }
