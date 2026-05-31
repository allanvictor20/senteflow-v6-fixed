"""
SenteFlow AI — Conversation State Machine
==========================================
Tracks each WhatsApp thread as a business context, not just a chat log.

States model the business relationship arc: from first inquiry through
payment, delivery, and completion — with full transition history.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field


class ConversationStatus(str, Enum):
    NEW = "new"
    INQUIRY = "inquiry"
    NEGOTIATION = "negotiation"
    ORDER_PENDING = "order_pending"
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_RECEIVED = "payment_received"
    PREPARING_DELIVERY = "preparing_delivery"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    DORMANT = "dormant"


# Allowed state transitions: current_state → set of valid next states
VALID_TRANSITIONS: dict[ConversationStatus, set[ConversationStatus]] = {
    ConversationStatus.NEW: {
        ConversationStatus.INQUIRY,
        ConversationStatus.ORDER_PENDING,
        ConversationStatus.FOLLOW_UP_REQUIRED,
    },
    ConversationStatus.INQUIRY: {
        ConversationStatus.NEGOTIATION,
        ConversationStatus.ORDER_PENDING,
        ConversationStatus.FOLLOW_UP_REQUIRED,
        ConversationStatus.DORMANT,
    },
    ConversationStatus.NEGOTIATION: {
        ConversationStatus.ORDER_PENDING,
        ConversationStatus.INQUIRY,
        ConversationStatus.DORMANT,
    },
    ConversationStatus.ORDER_PENDING: {
        ConversationStatus.AWAITING_PAYMENT,
        ConversationStatus.PAYMENT_RECEIVED,
        ConversationStatus.NEGOTIATION,
        ConversationStatus.FOLLOW_UP_REQUIRED,
    },
    ConversationStatus.AWAITING_PAYMENT: {
        ConversationStatus.PAYMENT_RECEIVED,
        ConversationStatus.FOLLOW_UP_REQUIRED,
        ConversationStatus.DORMANT,
    },
    ConversationStatus.PAYMENT_RECEIVED: {
        ConversationStatus.PREPARING_DELIVERY,
        ConversationStatus.COMPLETED,
    },
    ConversationStatus.PREPARING_DELIVERY: {
        ConversationStatus.IN_TRANSIT,
        ConversationStatus.COMPLETED,
    },
    ConversationStatus.IN_TRANSIT: {
        ConversationStatus.COMPLETED,
        ConversationStatus.FOLLOW_UP_REQUIRED,
    },
    ConversationStatus.COMPLETED: {
        ConversationStatus.NEW,  # New cycle starts
        ConversationStatus.INQUIRY,
    },
    ConversationStatus.FOLLOW_UP_REQUIRED: {
        ConversationStatus.INQUIRY,
        ConversationStatus.ORDER_PENDING,
        ConversationStatus.AWAITING_PAYMENT,
        ConversationStatus.DORMANT,
    },
    ConversationStatus.DORMANT: {
        ConversationStatus.INQUIRY,
        ConversationStatus.NEW,
    },
}

# Map EventType strings to the state they push the conversation into
EVENT_TO_STATE: dict[str, ConversationStatus] = {
    "customer_inquiry": ConversationStatus.INQUIRY,
    "negotiation": ConversationStatus.NEGOTIATION,
    "customer_order": ConversationStatus.ORDER_PENDING,
    "order_received": ConversationStatus.ORDER_PENDING,
    "payment_promise": ConversationStatus.AWAITING_PAYMENT,
    "payment_received": ConversationStatus.PAYMENT_RECEIVED,
    "payment": ConversationStatus.PAYMENT_RECEIVED,
    "delivery_update": ConversationStatus.IN_TRANSIT,
    "follow_up_required": ConversationStatus.FOLLOW_UP_REQUIRED,
    "complaint": ConversationStatus.FOLLOW_UP_REQUIRED,
}


class StateTransitionRecord(BaseModel):
    from_state: str
    to_state: str
    trigger_event_id: str
    trigger_event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    note: str = ""


class ConversationAggregate(BaseModel):
    """
    Full business context for a WhatsApp thread.
    Replaces simple message storage with stateful business tracking.
    """

    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str

    customer_id: str
    customer_name: Optional[str] = None

    # State machine
    current_state: ConversationStatus = ConversationStatus.NEW
    state_history: list[StateTransitionRecord] = Field(default_factory=list)

    # Active order reference
    active_order_id: Optional[str] = None

    # Pending tasks for this conversation
    pending_tasks: list[str] = Field(default_factory=list)   # task IDs

    # What we expect to happen next
    expected_next_action: str = ""

    # Message context
    last_message: Optional[str] = None
    last_interaction: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # Event history (IDs only — full events stored separately)
    event_ids: list[str] = Field(default_factory=list)

    # Clarification state (multi-turn)
    awaiting_clarification: bool = False
    clarification_question: Optional[str] = None
    clarification_original_text: Optional[str] = None

    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── State Machine ──────────────────────────────────────────────────────────

    def can_transition_to(self, new_state: ConversationStatus) -> bool:
        allowed = VALID_TRANSITIONS.get(self.current_state, set())
        return new_state in allowed

    def transition(
        self,
        new_state: ConversationStatus,
        event_id: str,
        event_type: str,
        note: str = "",
    ) -> bool:
        """
        Attempt a state transition. Returns True if successful.
        Logs invalid transitions but does NOT raise — the system keeps running.
        """
        if new_state == self.current_state:
            return True  # no-op

        if not self.can_transition_to(new_state):
            # Allow the transition anyway but record it as unexpected
            note = f"[unexpected transition] {note}"

        record = StateTransitionRecord(
            from_state=self.current_state.value,
            to_state=new_state.value,
            trigger_event_id=event_id,
            trigger_event_type=event_type,
            note=note,
        )
        self.state_history.append(record)
        self.current_state = new_state
        self.updated_at = datetime.utcnow().isoformat()
        return True

    def apply_event(self, event_type: str, event_id: str, entities: dict) -> Optional[ConversationStatus]:
        """
        Drive the state machine from an event type.
        Returns the new state if a transition occurred, else None.
        """
        self.event_ids.append(event_id)
        self.last_interaction = datetime.utcnow().isoformat()
        self.updated_at = self.last_interaction

        target = EVENT_TO_STATE.get(event_type)
        if not target:
            return None

        changed = self.transition(target, event_id, event_type)
        if changed:
            self._set_expected_next_action(target, entities)
        return target if changed else None

    def _set_expected_next_action(self, state: ConversationStatus, entities: dict) -> None:
        mapping = {
            ConversationStatus.INQUIRY: "Reply to customer inquiry",
            ConversationStatus.NEGOTIATION: "Finalize pricing / terms",
            ConversationStatus.ORDER_PENDING: "Confirm order details",
            ConversationStatus.AWAITING_PAYMENT: f"Collect payment ({entities.get('due_date', 'soon')})",
            ConversationStatus.PAYMENT_RECEIVED: "Prepare goods for dispatch",
            ConversationStatus.PREPARING_DELIVERY: "Dispatch order",
            ConversationStatus.IN_TRANSIT: "Confirm delivery",
            ConversationStatus.COMPLETED: "Record for follow-up",
            ConversationStatus.FOLLOW_UP_REQUIRED: "Follow up with customer",
            ConversationStatus.DORMANT: "Re-engage customer",
        }
        self.expected_next_action = mapping.get(state, "")
