"""
SenteFlow AI — BusinessEvent
==============================
The universal abstraction for all SME business communications.
Everything entering the system becomes a BusinessEvent — not just a Transaction.

This is the core architectural change: messages carry rich business meaning
beyond simple debits and credits.
"""

import uuid
from datetime import datetime

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from domain.events.event_types import (
    EventType,
    FINANCIAL_EVENTS,
    FOLLOWUP_REQUIRED_EVENTS,
)
from utils.clock import utc_now


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    INTERPRETED = "interpreted"
    COMPLETED = "completed"
    FAILED = "failed"


class BusinessEvent(BaseModel):
    """
    Universal representation of a business communication event.
    Replaces the narrow Transaction model as the system's core object.

    Every WhatsApp message → BusinessEvent → one or more downstream actions.
    """

    # Identity
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType = EventType.UNKNOWN

    # Source
    raw_message: str = ""
    source_type: str = "whatsapp"   # whatsapp | api | upload
    sender_id: str = ""
    business_id: str = ""
    timestamp: datetime = Field(default_factory=utc_now)

    # Extracted meaning
    entities: dict[str, Any] = Field(default_factory=dict)
    """
    Flexible entity bag. Examples:
      payment_received: {"amount": 50000, "payer": "Sarah", "currency": "UGX"}
      low_stock_alert:  {"item": "cement", "quantity": 2, "unit": "crates"}
      payment_promise:  {"debtor": "Brian", "amount": 70000, "due_date": "Friday"}
    """

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    context: dict[str, Any] = Field(default_factory=dict)
    """Enrichment context injected by the ContextEngine before interpretation."""

    operational_effects: list[str] = Field(default_factory=list)
    """Human-readable list of what this event should cause, e.g. ['Update debt balance', 'Send reminder']."""

    recommended_actions: list[str] = Field(default_factory=list)
    """Action keys for the ActionDispatcher, e.g. ['update_ledger', 'schedule_reminder']."""

    reasoning: str = ""
    """AI explanation of why this classification was made."""

    processing_status: ProcessingStatus = ProcessingStatus.PENDING

    # Optional: link back to a legacy Transaction id if one was created
    transaction_id: Optional[str] = None

    # ─── Helper methods ───────────────────────────────────────────────────────

    def is_financial(self) -> bool:
        """True for any event that affects money."""
        return self.event_type in FINANCIAL_EVENTS

    def requires_followup(self) -> bool:
        """True for events that need a scheduled reminder or further action."""
        return self.event_type in FOLLOWUP_REQUIRED_EVENTS

    def is_high_confidence(self) -> bool:
        """True when AI confidence is at least 80%."""
        return self.confidence >= 0.80

    def to_summary(self) -> str:
        """One-line human-readable summary for logs and WhatsApp replies."""
        etype = self.event_type.value.replace("_", " ").title()
        conf_pct = int(self.confidence * 100)
        entities_str = ", ".join(f"{k}={v}" for k, v in list(self.entities.items())[:3])
        return f"[{etype}] {entities_str} (confidence {conf_pct}%)"

    def to_storable(self) -> dict[str, Any]:
        """
        JSON-safe payload for persistence.

        `context` is deliberately excluded: it is request-scoped enrichment that
        can hold live objects (repositories, HTTP clients) which are not
        serializable, and re-storing it on every event would duplicate data that
        already lives in its own collections.
        """
        return self.model_dump(mode="json", exclude={"context"})


class EventResult(BaseModel):
    """
    Output produced after a BusinessEvent is fully processed.
    Returned by the ActionDispatcher so callers can send appropriate WhatsApp replies.
    """
    event_id: str
    success: bool
    actions_executed: list[str] = Field(default_factory=list)
    actions_failed: list[str] = Field(default_factory=list)
    whatsapp_reply: str = ""
    transaction_ids: list[str] = Field(default_factory=list)
    followup_scheduled: bool = False
    pending_approval: bool = False
    """True when a HIGH-RISK action was held back awaiting owner confirmation."""
    error: Optional[str] = None
