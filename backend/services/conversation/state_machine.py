"""
Conversation lifecycle state machine.

This module turns isolated WhatsApp events into a durable business flow:
inquiry -> order -> payment -> delivery -> completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from domain.events.event_types import EventType
from domain.models import (
    ConversationState,
    ConversationTimelineEntry,
    PendingAction,
)


@dataclass(frozen=True)
class StateTransition:
    state: ConversationState
    pending_action: PendingAction
    last_intent: str


class ConversationStateManager:
    """Applies finite-state transition rules and persists conversation snapshots."""

    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def apply_business_event(self, event: Any) -> dict[str, Any]:
        existing = self.repo.get_conversation(self.org_id, event.sender_id) or {}
        previous_state = self._coerce_state(existing.get("state"))
        transition = self._transition_for_event(event.event_type, previous_state, event.entities)
        active_order_id = (
            event.entities.get("order_id")
            or existing.get("active_order_id")
            or self._find_open_order(event.sender_id)
        )

        updates = {
            "conversation_id": event.sender_id,
            "customer_id": event.sender_id,
            "state": transition.state.value,
            "active_order_id": active_order_id,
            "last_intent": transition.last_intent,
            "pending_action": transition.pending_action.value,
            "last_event_id": event.event_id,
            "last_event_type": event.event_type.value,
            "last_message": event.raw_message,
            "linked_transaction_id": event.transaction_id,
            "entities": event.entities,
        }
        self.repo.upsert_conversation(self.org_id, event.sender_id, updates)

        timeline_entry = ConversationTimelineEntry(
            event_id=event.event_id,
            event_type=event.event_type.value,
            from_state=previous_state.value,
            to_state=transition.state.value,
            pending_action=transition.pending_action.value,
            active_order_id=active_order_id,
            related_transaction_ids=[event.transaction_id] if event.transaction_id else [],
            confidence=event.confidence,
            summary=event.to_summary(),
        )
        self.repo.append_conversation_timeline(self.org_id, event.sender_id, timeline_entry.model_dump(mode="json"))
        return updates

    def record_media_processed(
        self,
        sender_id: str,
        event_id: str,
        media_id: str,
        transaction_ids: list[str],
        summary: str,
        confidence: float = 0.7,
    ) -> dict[str, Any]:
        existing = self.repo.get_conversation(self.org_id, sender_id) or {}
        previous_state = self._coerce_state(existing.get("state"))
        next_state = (
            ConversationState.PAYMENT_RECEIVED
            if transaction_ids
            else previous_state
        )
        pending = (
            PendingAction.DELIVERY_PREPARATION
            if transaction_ids
            else PendingAction.PAYMENT_REVIEW
        )
        updates = {
            "conversation_id": sender_id,
            "customer_id": sender_id,
            "state": next_state.value,
            "pending_action": pending.value,
            "last_intent": "media_extraction",
            "last_event_id": event_id,
            "related_media_id": media_id,
            "related_transaction_ids": transaction_ids,
        }
        self.repo.upsert_conversation(self.org_id, sender_id, updates)
        self.repo.append_conversation_timeline(
            self.org_id,
            sender_id,
            ConversationTimelineEntry(
                event_id=event_id,
                event_type="media_processed",
                message_type="media",
                from_state=previous_state.value,
                to_state=next_state.value,
                pending_action=pending.value,
                active_order_id=existing.get("active_order_id"),
                related_transaction_ids=transaction_ids,
                related_media_id=media_id,
                confidence=confidence,
                summary=summary,
            ).model_dump(mode="json"),
        )
        return updates

    def _transition_for_event(
        self,
        event_type: EventType,
        previous_state: ConversationState,
        entities: dict[str, Any],
    ) -> StateTransition:
        if event_type in {EventType.CUSTOMER_ORDER, EventType.ORDER_RECEIVED, EventType.SUPPLIER_MESSAGE}:
            return StateTransition(
                ConversationState.AWAITING_PAYMENT,
                PendingAction.CUSTOMER_PAYMENT,
                "purchase",
            )
        if event_type == EventType.PAYMENT_PROMISE:
            return StateTransition(
                ConversationState.AWAITING_PAYMENT,
                PendingAction.CUSTOMER_PAYMENT,
                "payment_promise",
            )
        if event_type == EventType.PAYMENT_RECEIVED:
            return StateTransition(
                ConversationState.PAYMENT_RECEIVED,
                PendingAction.DELIVERY_PREPARATION,
                "payment",
            )
        if event_type == EventType.DEBT_CREATED:
            return StateTransition(
                ConversationState.AWAITING_PAYMENT,
                PendingAction.CUSTOMER_PAYMENT,
                "debt",
            )
        if event_type in {
            EventType.CUSTOMER_INQUIRY,
            EventType.NEGOTIATION,
            EventType.COMPLAINT,
            EventType.APPOINTMENT_REQUEST,
        }:
            return StateTransition(
                ConversationState.PENDING_INQUIRY,
                PendingAction.BUSINESS_REPLY,
                "inquiry",
            )
        if event_type in {EventType.INVENTORY_UPDATE, EventType.LOW_STOCK_ALERT, EventType.BUSINESS_NOTE}:
            return StateTransition(previous_state, PendingAction.NONE, event_type.value)
        if event_type == EventType.DELIVERY_UPDATE:
            status = entities.get("delivery_status")
            if status == "in_transit":
                return StateTransition(
                    ConversationState.IN_TRANSIT,
                    PendingAction.DELIVERY_CONFIRMATION,
                    "delivery",
                )
            if status == "delivered":
                return StateTransition(
                    ConversationState.DELIVERED,
                    PendingAction.CUSTOMER_FOLLOWUP,
                    "delivery",
                )
        if entities.get("delivery_status") == "in_transit":
            return StateTransition(
                ConversationState.IN_TRANSIT,
                PendingAction.DELIVERY_CONFIRMATION,
                "delivery",
            )
        if entities.get("delivery_status") == "delivered":
            return StateTransition(
                ConversationState.DELIVERED,
                PendingAction.CUSTOMER_FOLLOWUP,
                "delivery",
            )
        return StateTransition(previous_state, PendingAction.CUSTOMER_FOLLOWUP, event_type.value)

    def _find_open_order(self, sender_id: str) -> Optional[str]:
        """
        IDEA 13 — list_orders() Collection Scan Bug Fix:
        Previously did a Python-level filter over 25 docs on every message.
        For orgs with 26+ orders, the matching order could be silently missed,
        causing duplicate order creation.

        Now uses a direct Firestore query filtered by customer_id + status.
        """
        try:
            docs = (
                self.repo._db
                .collection("organizations").document(self.org_id)
                .collection("orders")
                .where("customer_id", "==", sender_id)
                .where("status", "not-in", ["completed", "cancelled"])
                .limit(1)
                .get()
            )
            return docs[0].id if docs else None
        except Exception:
            # Firestore compound query may not have an index yet — fall back
            # to the old approach so existing deployments don't break
            for order in self.repo.list_orders(self.org_id, limit=50):
                if (
                    order.get("customer_id") == sender_id
                    and order.get("status") not in {"completed", "cancelled"}
                ):
                    return order.get("id") or order.get("order_id")
            return None

    def _coerce_state(self, value: Optional[str]) -> ConversationState:
        if not value:
            return ConversationState.PENDING_INQUIRY
        try:
            return ConversationState(value)
        except ValueError:
            legacy_map = {
                "active": ConversationState.PENDING_INQUIRY,
                "pending_order": ConversationState.NEGOTIATING,
                "awaiting_delivery": ConversationState.PREPARING_DELIVERY,
                "unresolved_inquiry": ConversationState.PENDING_INQUIRY,
            }
            return legacy_map.get(value, ConversationState.PENDING_INQUIRY)
