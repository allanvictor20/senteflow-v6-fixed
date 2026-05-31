"""
SenteFlow AI — ConversationAggregate Repository
=================================================
Firestore persistence for the full ConversationAggregate state machine.
"""

import logging
from datetime import datetime
from typing import Optional

from domain.conversations.state import ConversationAggregate

logger = logging.getLogger(__name__)


class ConversationAggregateRepository:

    def __init__(self, db):
        self._db = db

    def _col(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("conversation_aggregates")
        )

    def save(self, org_id: str, agg: ConversationAggregate) -> str:
        agg.org_id = org_id
        agg.updated_at = datetime.utcnow().isoformat()
        self._col(org_id).document(agg.conversation_id).set(
            agg.model_dump(mode="json"), merge=True
        )
        return agg.conversation_id

    def get(self, org_id: str, conversation_id: str) -> Optional[ConversationAggregate]:
        doc = self._col(org_id).document(conversation_id).get()
        if not doc.exists:
            return None
        return ConversationAggregate(**doc.to_dict())

    def get_by_customer(self, org_id: str, customer_id: str) -> Optional[ConversationAggregate]:
        """Get the most recent active aggregate for a customer."""
        docs = (
            self._col(org_id)
            .where("customer_id", "==", customer_id)
            .order_by("last_interaction", direction="DESCENDING")
            .limit(1)
            .get()
        )
        for d in docs:
            try:
                return ConversationAggregate(**d.to_dict())
            except Exception as e:
                logger.warning("aggregate_parse_error", extra={"error": str(e)})
        return None

    def get_or_create(self, org_id: str, customer_id: str, customer_name: Optional[str] = None) -> ConversationAggregate:
        existing = self.get_by_customer(org_id, customer_id)
        if existing:
            return existing
        agg = ConversationAggregate(
            org_id=org_id,
            customer_id=customer_id,
            customer_name=customer_name,
        )
        self.save(org_id, agg)
        return agg

    def list_requiring_followup(self, org_id: str, limit: int = 50) -> list[ConversationAggregate]:
        from domain.conversations.state import ConversationStatus
        docs = (
            self._col(org_id)
            .where("current_state", "==", ConversationStatus.FOLLOW_UP_REQUIRED.value)
            .order_by("last_interaction")
            .limit(limit)
            .get()
        )
        return [ConversationAggregate(**d.to_dict()) for d in docs]