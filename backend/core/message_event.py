"""
SenteFlow AI — Core Message Event Schema
==========================================
Normalizes all incoming WhatsApp events into a single internal format.
This schema is the contract between the transport layer and the AI/business layers.

OpenWA-specific fields NEVER appear beyond this module.
All layers downstream work only with MessageEvent objects.
"""

from datetime import datetime

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from utils.clock import utc_now


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VOICE = "voice"         # Voice note (PTT — push to talk)
    DOCUMENT = "document"
    VIDEO = "video"
    STICKER = "sticker"
    LOCATION = "location"
    UNKNOWN = "unknown"


class MessageEvent(BaseModel):
    """
    Canonical internal representation of any WhatsApp message.
    Created by the webhook handler; consumed by all downstream layers.

    Fields:
        event_id        — unique ID for this event (from OpenWA or generated)
        message_id      — WhatsApp message ID (for reactions/replies)
        sender_id       — WhatsApp ID, e.g. "256700000000@c.us"
        sender_name     — Display name if available
        chat_id         — Chat/group ID to reply to
        message_type    — Normalized message type (text, image, audio, etc.)
        text            — Text content (for text messages or captions)
        media_url       — URL to download media (images, audio, documents)
        media_mime_type — MIME type of the media
        media_filename  — Original filename for documents
        timestamp       — Message timestamp
        raw_payload     — Original OpenWA payload (for debugging only)
    """

    event_id: str = Field(..., description="Unique identifier for this event")
    message_id: str = Field(..., description="WhatsApp message ID")
    sender_id: str = Field(..., description="Sender WhatsApp ID")
    sender_name: Optional[str] = Field(None, description="Sender display name")
    chat_id: Optional[str] = Field(None, description="Chat ID to reply to")
    message_type: MessageType = Field(MessageType.UNKNOWN)
    text: Optional[str] = Field(None, description="Text content or caption")
    media_url: Optional[str] = Field(None, description="Media download URL")
    media_mime_type: Optional[str] = Field(None)
    media_filename: Optional[str] = Field(None)
    timestamp: datetime = Field(default_factory=utc_now)

    # Never expose raw_payload in business logic — for debugging only
    raw_payload: Optional[dict] = Field(None, exclude=True)

    def model_post_init(self, __context) -> None:
        if not self.chat_id:
            self.chat_id = self.sender_id

    @property
    def is_media(self) -> bool:
        return self.message_type in {
            MessageType.IMAGE,
            MessageType.AUDIO,
            MessageType.VOICE,
            MessageType.DOCUMENT,
            MessageType.VIDEO,
        }

    @property
    def is_receipt_candidate(self) -> bool:
        """True if this message might contain a receipt or financial document."""
        return self.message_type in {MessageType.IMAGE, MessageType.DOCUMENT}

    @property
    def is_voice_note(self) -> bool:
        return self.message_type in {MessageType.AUDIO, MessageType.VOICE}

    @property
    def display_sender(self) -> str:
        return self.sender_name or self.sender_id.split("@")[0]
