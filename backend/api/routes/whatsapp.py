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

All actual processing happens in the background task.
"""

import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from integrations.whatsapp.message_normalizer import normalize_message
from integrations.whatsapp.message_router import MessageRouter
from integrations.whatsapp.client import EvolutionClient
from core.message_event import MessageEvent
from core.errors import StructuredLogger

logger = StructuredLogger(__name__)

whatsapp_router = APIRouter(prefix="/api/webhooks", tags=["whatsapp"])

# These are set at app startup (see main.py)
_wa_client: EvolutionClient = None
_router: MessageRouter = None


def set_whatsapp_dependencies(wa_client: EvolutionClient, message_router: MessageRouter):
    global _wa_client, _router
    _wa_client = wa_client
    _router = message_router


# ─── Webhook Endpoint ─────────────────────────────────────────────────────────

@whatsapp_router.post("/whatsapp")
@whatsapp_router.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Evolution API posts all incoming WhatsApp events here.
    Must respond in < 5 seconds or Evolution API may retry.
    Heavy work (AI extraction) is queued as a background task.
    """
    raw_body = await request.body()

    # ── HMAC signature check (when WEBHOOK_SECRET is set) ──────────────────
    from integrations.whatsapp.webhook_handler import verify_webhook_signature
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
    sig_header = request.headers.get("x-webhook-signature", "")
    if not verify_webhook_signature(raw_body, sig_header or None, webhook_secret or None):
        logger.warning("webhook_signature_invalid")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── Fallback: plain API key check (Evolution global key) ───────────────
    try:
        payload = __import__("json").loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    expected_key = os.environ.get("EVOLUTION_API_KEY", "")
    if not webhook_secret and expected_key:
        incoming_key = (
            request.headers.get("apikey")
            or request.headers.get("x-api-key")
            or payload.get("apikey", "")
        )
        if incoming_key != expected_key:
            logger.warning(
                "webhook_unauthorized",
                incoming_key=incoming_key[:6] + "***" if incoming_key else "missing",
            )
            raise HTTPException(status_code=401, detail="Unauthorized")

    event = normalize_message(payload)
    if event is None:
        return JSONResponse({"status": "ignored"}, status_code=200)

    logger.info(
        "webhook_received",
        sender=event.display_sender,
        type=event.message_type.value,
        event_id=event.event_id,
    )

    if _router:
        created, queue_id = _router.repo.enqueue_webhook_event(
            org_id=_router.org_id,
            event_id=event.event_id,
            payload=payload,
            normalized_event=event.model_dump(mode="json"),
        )
        if created:
            background_tasks.add_task(_process_queued_event, queue_id)
        else:
            return JSONResponse({"status": "duplicate", "event_id": event.event_id}, status_code=200)
    else:
        logger.error("message_router_not_initialized")

    return JSONResponse({"status": "queued", "event_id": event.event_id}, status_code=200)

async def _process_queued_event(queue_id: str):
    if not _router:
        logger.error("message_router_not_initialized")
        return

    repo = _router.repo
    org_id = _router.org_id
    if not repo.mark_webhook_processing(org_id, queue_id):
        return

    queued = repo.get_queued_webhook_event(org_id, queue_id)
    if not queued:
        logger.error("queued_event_missing", queue_id=queue_id)
        return

    try:
        event = MessageEvent(**queued["normalized_event"])
        await _router.route(event)
        repo.mark_webhook_completed(org_id, queue_id)
    except Exception as exc:
        logger.error("queued_event_failed", queue_id=queue_id, error=str(exc))
        repo.mark_webhook_failed(org_id, queue_id, str(exc))


# ─── Session Status Endpoint ──────────────────────────────────────────────────

@whatsapp_router.get("/whatsapp/status")
async def whatsapp_status():
    """Check Evolution API session health."""
    if not _wa_client:
        return JSONResponse({"status": "not_configured"}, status_code=503)

    status = await _wa_client.get_session_status()
    health = await _wa_client.health_check()

    return {
        "session": status,
        "health": health,
        "configured": True,
    }
