"""Evolution API payload normalizer."""

from typing import Optional

from core.message_event import MessageEvent
from integrations.whatsapp.webhook_handler import normalize_evolution_event


def normalize_message(payload: dict) -> Optional[MessageEvent]:
    """Normalize a raw Evolution API webhook payload into a MessageEvent."""
    return normalize_evolution_event(payload)


__all__ = ["MessageEvent", "normalize_message"]
