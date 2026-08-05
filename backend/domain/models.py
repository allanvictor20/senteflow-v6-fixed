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


from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from utils.clock import utc_now


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

    # Extraction can surface fields we haven't modelled yet; keep them rather
    # than dropping data on the floor.
    model_config = ConfigDict(extra="allow")


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
    ocr: float = 0.0
    overall: float = 0.0

    @property
    def mean(self) -> float:
        """Average of the scores that were actually populated."""
        scores = [
            self.amount, self.currency, self.transaction_type,
            self.category, self.payer, self.payee, self.date, self.description,
        ]
        populated = [s for s in scores if s > 0]
        return sum(populated) / len(populated) if populated else 0.0

    def _score(self) -> float:
        return self.overall if self.overall > 0 else self.mean

    @property
    def label(self) -> str:
        """Human-readable confidence band, used by the review UI."""
        score = self._score()
        if score >= 0.85:
            return "high"
        if score >= 0.6:
            return "medium"
        return "low"

    @property
    def color(self) -> str:
        """Hex colour matching the confidence band."""
        return {"high": "#10b981", "medium": "#f59e0b", "low": "#f43f5e"}[self.label]


# ── SourceTrace ───────────────────────────────────────────────────────────────

class SourceTrace(BaseModel):
    """
    Provenance metadata: where did this extraction come from?

    Field names match what ai/extractor.py writes; the older aliases
    (file_name, raw_text, extraction_version) are kept as read-only
    properties so existing readers keep working.
    """
    source_type: str = "whatsapp"       # whatsapp | upload | api
    upload_session_id: Optional[str] = None
    source_file_name: Optional[str] = None
    source_mime_type: Optional[str] = None
    transcript_snippet: Optional[str] = None
    media_id: Optional[str] = None
    page_number: Optional[int] = None
    extraction_model: str = "llama-3.3-70b-versatile"
    extraction_prompt_version: str = "1"

    @property
    def file_name(self) -> Optional[str]:
        return self.source_file_name

    @property
    def raw_text(self) -> Optional[str]:
        return self.transcript_snippet

    @property
    def extraction_version(self) -> str:
        return self.extraction_prompt_version


# ── ExtractionResult ─────────────────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """
    Returned by the AI extraction layer for a receipt/voice note.

    `input_type`, `summary`, `language_detected`, `raw_transcript` and
    `confidence` are consumed by the WhatsApp reply builder and the
    /extract HTTP route, so they are part of the contract — not optional extras.
    """
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    upload_session_id: Optional[str] = None
    input_type: str = "unknown"          # text | image | pdf | audio
    transactions: list[Transaction] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    summary: str = ""
    language_detected: str = "en"
    raw_transcript: Optional[str] = None
    confidence: float = 0.0
    source_trace: Optional[SourceTrace] = None
    raw_llm_response: Optional[str] = None
    processing_time_ms: Optional[float] = None


# ── FinancialSummary ──────────────────────────────────────────────────────────

class FinancialSummary(BaseModel):
    """
    Aggregated income/expense snapshot for an org.

    `total_income` / `total_expenses` / `balance` are the canonical fields —
    they are what the WhatsApp summary reply and the dashboard KPI row read.
    `total_sales` and `total_received` break income down by source.
    """
    org_id: str = ""
    total_income: float = 0.0
    total_expenses: float = 0.0
    balance: float = 0.0
    total_sales: float = 0.0
    total_received: float = 0.0
    pending_amount: float = 0.0
    members_paid: int = 0
    members_pending: int = 0
    categories: dict[str, float] = Field(default_factory=dict)
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    @property
    def net(self) -> float:
        """Alias kept for callers written against the old dict shape."""
        return self.balance


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
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


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
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())
