"""WhatsApp media download and temporary storage helpers."""

from typing import Optional

from core.message_event import MessageEvent, MessageType
from services.llm.media_processor import download_and_save_media


def media_source_hint(event: MessageEvent) -> str:
    if event.message_type in {MessageType.VOICE, MessageType.AUDIO}:
        return "voice_note"
    if event.message_type == MessageType.DOCUMENT:
        return "document"
    if event.message_type == MessageType.IMAGE:
        return "receipt"
    return "upload"


async def save_whatsapp_media(event: MessageEvent, wa_client) -> Optional[str]:
    """Download a WhatsApp media attachment and return a local storage path."""
    if not event.media_url:
        return None
    return await download_and_save_media(
        media_url=event.media_url,
        mime_type=event.media_mime_type,
        sender_id=event.sender_id,
        source_hint=media_source_hint(event),
        wa_client=wa_client,
    )
