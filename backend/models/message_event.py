"""Conversation-first message event model.

This is the stable business-layer contract. Transport adapters such as OpenWA
may carry many fields, but core workflows should start from this compact shape:
one user, one message, optional text, optional media, and a timestamp.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InputType(str, Enum):
    TEXT = "text"
    VOICE_NOTE = "voice_note"
    RECEIPT = "receipt"
    IMAGE = "image"
    DOCUMENT = "document"
    PDF = "pdf"
    UNKNOWN = "unknown"


class MessageEvent(BaseModel):
    user_id: str = Field(..., description="Business user or WhatsApp sender id")
    input_type: InputType = Field(InputType.UNKNOWN)
    text: Optional[str] = None
    media_path: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    confidence_hint: Optional[float] = Field(None, ge=0.0, le=1.0)

    @classmethod
    def from_whatsapp(cls, event: object, media_path: Optional[str] = None) -> "MessageEvent":
        """Create the compact event from the richer WhatsApp transport event."""
        message_type = getattr(event, "message_type", None)
        value = getattr(message_type, "value", str(message_type or "unknown"))
        input_type = {
            "text": InputType.TEXT,
            "voice": InputType.VOICE_NOTE,
            "audio": InputType.VOICE_NOTE,
            "image": InputType.RECEIPT,
            "document": InputType.DOCUMENT,
        }.get(value, InputType.UNKNOWN)

        return cls(
            user_id=getattr(event, "sender_id", ""),
            input_type=input_type,
            text=getattr(event, "text", None),
            media_path=media_path,
            timestamp=getattr(event, "timestamp", datetime.utcnow()),
            chat_id=getattr(event, "chat_id", None),
            message_id=getattr(event, "message_id", None),
        )
