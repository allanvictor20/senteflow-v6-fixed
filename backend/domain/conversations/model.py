"""
SenteFlow AI - Business Conversation
====================================
Tracks an active WhatsApp thread with a customer or supplier.
"""



from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field
from utils.clock import utc_now


class ConversationStage(str, Enum):
    UNKNOWN = "unknown"
    INQUIRY = "inquiry"
    NEGOTIATION = "negotiation"
    ORDER_PENDING = "order_pending"
    AWAITING_PAYMENT = "awaiting_payment"
    DELIVERY = "delivery"
    FOLLOW_UP = "follow_up"
    COMPLETED = "completed"


class PendingAction(str, Enum):
    NONE = "none"
    REPLY_TO_CUSTOMER = "reply_to_customer"
    CONFIRM_ORDER = "confirm_order"
    COLLECT_PAYMENT = "collect_payment"
    PREPARE_DELIVERY = "prepare_delivery"
    FOLLOW_UP = "follow_up"


class BusinessConversation(BaseModel):
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    participant_id: str
    participant_name: Optional[str] = None
    participant_type: str = "customer"
    stage: ConversationStage = ConversationStage.UNKNOWN
    pending_action: PendingAction = PendingAction.NONE
    subject: str = ""
    event_ids: list[str] = Field(default_factory=list)
    expected_payment_amount: Optional[float] = None
    expected_payment_date: Optional[str] = None
    items_discussed: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: utc_now().isoformat())
    last_message_at: str = Field(default_factory=lambda: utc_now().isoformat())
    resolved_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_active(self) -> bool:
        return self.stage != ConversationStage.COMPLETED

    def needs_action(self) -> bool:
        return self.pending_action != PendingAction.NONE
