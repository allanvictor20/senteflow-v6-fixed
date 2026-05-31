"""
SenteFlow AI - Event Repository
===============================
Firestore operations for BusinessEvent objects.
Path: organizations/{org_id}/events/{event_id}
"""

import asyncio
import logging
from typing import Optional

from domain.events.business_event import BusinessEvent

logger = logging.getLogger(__name__)


class EventRepository:
    def __init__(self, db):
        self._db = db

    def _collection(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("events")
        )

    async def save_event(self, org_id: str, event: BusinessEvent | dict) -> str:
        data = event.model_dump(mode="json") if isinstance(event, BusinessEvent) else dict(event)
        event_id = data.get("event_id") or data.get("id")
        await asyncio.to_thread(self._collection(org_id).document(event_id).set, data, True)
        logger.info("event_saved", extra={"event_id": event_id, "event_type": data.get("event_type")})
        return event_id

    async def get_event(self, org_id: str, event_id: str) -> Optional[dict]:
        doc = await asyncio.to_thread(self._collection(org_id).document(event_id).get)
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    async def list_events(
        self,
        org_id: str,
        event_type: Optional[str] = None,
        sender_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        ref = self._collection(org_id)
        if event_type:
            ref = ref.where("event_type", "==", event_type)
        if sender_id:
            ref = ref.where("sender_id", "==", sender_id)
        docs = await asyncio.to_thread(ref.order_by("timestamp", direction="DESCENDING").limit(limit).get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]

    async def list_followups(self, org_id: str, limit: int = 50) -> list[dict]:
        ref = (
            self._collection(org_id)
            .where("event_type", "in", ["payment_promise", "debt_created", "customer_order", "complaint"])
            .order_by("timestamp", direction="DESCENDING")
            .limit(limit)
        )
        docs = await asyncio.to_thread(ref.get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]
