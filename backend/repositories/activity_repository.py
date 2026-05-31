"""
SenteFlow AI - Activity Repository
==================================
Compatibility repository for older callers.

Activities now use the BusinessEvent shape internally. New code should prefer
repositories.event_repository.EventRepository, but this wrapper keeps existing
imports alive.
"""

import logging
from typing import Optional

from domain.events.business_event import BusinessEvent as BusinessActivity

logger = logging.getLogger(__name__)


class ActivityRepository:
    def __init__(self, db):
        self._db = db

    def _collection(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("activities")
        )

    def save_activity(self, org_id: str, activity: BusinessActivity) -> str:
        event_id = getattr(activity, "event_id", None) or getattr(activity, "activity_id", None)
        doc_ref = self._collection(org_id).document(event_id)
        doc_ref.set(activity.model_dump(mode="json"))
        event_type = getattr(activity, "event_type", None) or getattr(activity, "activity_type", None)
        logger.info("activity_saved", extra={"id": event_id, "type": event_type})
        return event_id

    def list_activities(
        self,
        org_id: str,
        activity_type: Optional[str] = None,
        sender_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        ref = self._collection(org_id)
        if activity_type:
            ref = ref.where("event_type", "==", activity_type)
        if sender_id:
            ref = ref.where("sender_id", "==", sender_id)
        docs = ref.order_by("timestamp", direction="DESCENDING").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def get_customer_activities(self, org_id: str, customer_id: str, limit: int = 20) -> list[dict]:
        return self.list_activities(org_id, sender_id=customer_id, limit=limit)