"""
SenteFlow AI - Evolution API Webhook Handler
============================================
Receives Evolution API webhook payloads and converts processable inbound
messages into normalized MessageEvent objects.
"""

import logging
import os
import uuid
import hashlib
import hmac
from datetime import datetime

from typing import Optional

from core.message_event import MessageEvent, MessageType
from utils.clock import utc_now

logger = logging.getLogger(__name__)


_EVOLUTION_MSG_TYPE_MAP: dict[str, MessageType] = {
    "conversation": MessageType.TEXT,
    "extendedTextMessage": MessageType.TEXT,
    "imageMessage": MessageType.IMAGE,
    "audioMessage": MessageType.AUDIO,
    "videoMessage": MessageType.VIDEO,
    "documentMessage": MessageType.DOCUMENT,
    "stickerMessage": MessageType.STICKER,
    "locationMessage": MessageType.LOCATION,
    "contactMessage": MessageType.UNKNOWN,
    "reactionMessage": MessageType.UNKNOWN,
}

def verify_webhook_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    webhook_secret: Optional[str],
) -> bool:
    """
    Verify the Evolution API webhook HMAC-SHA256 signature.

    Evolution API signs the raw request body with the webhook secret you
    configure via WEBHOOK_SECRET in your .env.  The signature arrives in
    the 'x-webhook-signature' header as 'sha256=<hex>'.

    Returns True if:
      - The computed HMAC matches the provided signature
      - No webhook secret is configured AND we are not in production

    Returns False (reject the request) if a secret is set but the
    signature is missing or does not match, or if no secret is configured
    while ENVIRONMENT=production. Silently accepting unsigned webhooks in
    production would let anyone post fabricated messages into the pipeline.
    """
    if not webhook_secret:
        environment = os.environ.get("ENVIRONMENT", "production").lower()
        if environment in ("production", "prod"):
            logger.error(
                "webhook_secret_missing_in_production",
                extra={"hint": "Set WEBHOOK_SECRET to accept Evolution API webhooks"},
            )
            return False
        return True  # secret not configured — allow (dev mode only)
    if not signature_header:
        return False
    try:
        scheme, provided_digest = signature_header.split("=", 1)
        if scheme != "sha256":
            return False
        expected = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, provided_digest)
    except Exception:
        return False


def normalize_evolution_event(payload: dict) -> Optional[MessageEvent]:
    """
    Convert a raw Evolution API webhook payload into a MessageEvent.

    Returns None for non-message events, outgoing bot messages, and
    non-processable message types.
    """
    try:
        event_name = payload.get("event", "")
        if event_name != "messages.upsert":
            logger.debug("evolution_event_ignored", extra={"event": event_name})
            return None

        data = payload.get("data", {})
        key = data.get("key", {})

        if key.get("fromMe", False):
            return None

        message_id = key.get("id") or str(uuid.uuid4())
        sender_id = key.get("remoteJid", "unknown@s.whatsapp.net")
        sender_name = data.get("pushName") or None
        chat_id = sender_id

        message_type_raw = data.get("messageType", "")
        message_obj = data.get("message", {})
        (
            message_type,
            text,
            media_url,
            media_mime_type,
            media_filename,
        ) = _extract_message_content(message_type_raw, message_obj)

        ts_raw = data.get("messageTimestamp")
        timestamp = datetime.utcfromtimestamp(int(ts_raw)) if ts_raw else utc_now()

        event = MessageEvent(
            event_id=str(uuid.uuid4()),
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            message_type=message_type,
            text=text,
            media_url=media_url,
            media_mime_type=media_mime_type,
            media_filename=media_filename,
            timestamp=timestamp,
            raw_payload=payload,
        )

        logger.info(
            "webhook_event_normalized",
            extra={
                "event_id": event.event_id,
                "sender": event.display_sender,
                "type": event.message_type.value,
            },
        )
        return event
    except Exception as exc:
        logger.error(
            "evolution_normalize_failed",
            extra={"error": str(exc), "event": payload.get("event", "?")},
        )
        return None


def _extract_message_content(
    message_type_raw: str,
    message_obj: dict,
) -> tuple[MessageType, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract type, text, media URL, MIME type, and filename."""
    text = None
    media_url = None
    media_mime_type = None
    media_filename = None
    message_type = _EVOLUTION_MSG_TYPE_MAP.get(message_type_raw, MessageType.UNKNOWN)

    if message_type_raw == "conversation":
        text = message_obj.get("conversation") or ""
        text = text.strip() or None
    elif message_type_raw == "extendedTextMessage":
        inner = message_obj.get("extendedTextMessage", {})
        text = inner.get("text", "").strip() or None
    elif message_type_raw == "imageMessage":
        img = message_obj.get("imageMessage", {})
        media_url = img.get("url")
        raw_mime = img.get("mimetype", "image/jpeg")
        media_mime_type = raw_mime.split(";")[0].strip()
        text = img.get("caption", "").strip() or None
    elif message_type_raw == "audioMessage":
        audio = message_obj.get("audioMessage", {})
        media_url = audio.get("url")
        raw_mime = audio.get("mimetype", "audio/ogg")
        media_mime_type = raw_mime.split(";")[0].strip()
        message_type = MessageType.VOICE if audio.get("ptt", False) else MessageType.AUDIO
    elif message_type_raw == "documentMessage":
        doc = message_obj.get("documentMessage", {})
        media_url = doc.get("url")
        raw_mime = doc.get("mimetype", "application/octet-stream")
        media_mime_type = raw_mime.split(";")[0].strip()
        media_filename = doc.get("fileName") or doc.get("title")
        text = doc.get("caption", "").strip() or None
    elif message_type_raw == "videoMessage":
        vid = message_obj.get("videoMessage", {})
        media_url = vid.get("url")
        raw_mime = vid.get("mimetype", "video/mp4")
        media_mime_type = raw_mime.split(";")[0].strip()
        text = vid.get("caption", "").strip() or None
    elif message_type_raw == "locationMessage":
        message_type = MessageType.LOCATION

    return message_type, text, media_url, media_mime_type, media_filename
