"""
SenteFlow AI — domain/models.py
================================
Compatibility shim: provides all model classes that legacy code still imports
from 'domain.models'. New code should import directly from the specific
domain sub-packages (domain.events, domain.conversations, etc.).

Classes provided:
  Transaction             — lightweight ledger entry (used by reply_generator,
                            action_dispatcher, ai/extractor, validators)
  ExtractionResult        — returned by the AI extraction layer (ai/extractor)
  FieldConfidence         — per-field confidence scores attached to Transaction
  SourceTrace             — provenance metadata for an extraction
  FinancialSummary        — aggregated income/expense snapshot
  EntityLink              — a typed relationship between two domain objects
  ConversationState       — state enum used by state_machine
  ConversationTimelineEntry — single entry in a conversation history
  PendingAction           — pending action enum used by state_machine
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Transaction ───────────────────────────────────────────────────────────────

class Transaction(BaseModel):
    """Lightweight ledger entry. Created by UpdateLedgerTool from a BusinessEvent."""
    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    amount: float
    currency: str = "UGX"
    transaction_type: str = "payment"   # payment | expense | debt_created | payment_promise
    category: str = "other"
    description: str = ""
    payer: Optional[str] = None
    payee: Optional[str] = None
    member_name: Optional[str] = None
    date: Optional[str] = None
    notes: Optional[str] = None
    status: str = "pending"             # pending | approved | rejected
    org_id: Optional[str] = None
    field_confidence: Optional["FieldConfidence"] = None

    class Config:
        extra = "allow"


# ── FieldConfidence ───────────────────────────────────────────────────────────

class FieldConfidence(BaseModel):
    """Per-field confidence scores (0.0–1.0) for an extracted Transaction."""
    amount: float = 0.0
    currency: float = 0.0
    transaction_type: float = 0.0
    category: float = 0.0
    payer: float = 0.0
    payee: float = 0.0
    date: float = 0.0
    description: float = 0.0
    overall: float = 0.0


# ── SourceTrace ───────────────────────────────────────────────────────────────

class SourceTrace(BaseModel):
    """Provenance metadata: where did this extraction come from?"""
    source_type: str = "whatsapp"       # whatsapp | upload | api
    file_name: Optional[str] = None
    media_id: Optional[str] = None
    page_number: Optional[int] = None
    raw_text: Optional[str] = None
    extraction_model: str = "gemini-2.0-flash"
    extraction_version: str = "1"


# ── ExtractionResult ─────────────────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """Returned by the AI extraction layer for a receipt/voice note."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    transactions: list[Transaction] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    source_trace: Optional[SourceTrace] = None
    raw_llm_response: Optional[str] = None
    processing_time_ms: Optional[float] = None


# ── FinancialSummary ──────────────────────────────────────────────────────────

class FinancialSummary(BaseModel):
    """Aggregated income/expense snapshot for an org."""
    org_id: str = ""
    total_income: float = 0.0
    total_expenses: float = 0.0
    balance: float = 0.0
    pending_amount: float = 0.0
    members_paid: int = 0
    members_pending: int = 0
    categories: dict[str, float] = Field(default_factory=dict)
    period_start: Optional[str] = None
    period_end: Optional[str] = None


# ── EntityLink ────────────────────────────────────────────────────────────────

class EntityLink(BaseModel):
    """A typed relationship between two domain objects (e.g. event → order)."""
    link_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_entity_type: str
    source_entity_id: str
    target_entity_type: str
    target_entity_id: str
    relationship: str                   # e.g. "sent_by", "updates", "pays_for"
    confidence: float = 1.0
    reasons: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── ConversationState / PendingAction / ConversationTimelineEntry ─────────────
# These mirror the enums used by services/conversation/state_machine.py.

class ConversationState(str, Enum):
    PENDING_INQUIRY      = "pending_inquiry"
    NEGOTIATING          = "negotiating"
    AWAITING_PAYMENT     = "awaiting_payment"
    PAYMENT_RECEIVED     = "payment_received"
    PREPARING_DELIVERY   = "preparing_delivery"
    IN_TRANSIT           = "in_transit"
    DELIVERED            = "delivered"
    COMPLETED            = "completed"
    DORMANT              = "dormant"


class PendingAction(str, Enum):
    NONE                 = "none"
    CUSTOMER_PAYMENT     = "customer_payment"
    BUSINESS_REPLY       = "business_reply"
    DELIVERY_PREPARATION = "delivery_preparation"
    DELIVERY_CONFIRMATION= "delivery_confirmation"
    CUSTOMER_FOLLOWUP    = "customer_followup"
    PAYMENT_REVIEW       = "payment_review"


class ConversationTimelineEntry(BaseModel):
    """Single entry in a conversation's state-transition history."""
    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str = ""
    event_type: str = ""
    message_type: str = "text"
    from_state: str = ""
    to_state: str = ""
    pending_action: str = PendingAction.NONE.value
    active_order_id: Optional[str] = None
    related_transaction_ids: list[str] = Field(default_factory=list)
    related_media_id: Optional[str] = None
    confidence: float = 0.0
    summary: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
