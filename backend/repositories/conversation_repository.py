"""Firestore operations for BusinessConversation objects."""

import asyncio
from datetime import datetime
from typing import Optional

from domain.conversations.model import BusinessConversation


class ConversationRepository:
    def __init__(self, db):
        self._db = db

    def _collection(self, org_id: str):
        return self._db.collection("organizations").document(org_id).collection("conversations")

    async def save_conversation(self, org_id: str, conv: BusinessConversation) -> str:
        doc_ref = self._collection(org_id).document(conv.conversation_id)
        data = conv.model_dump(mode="json")
        data["last_message_at"] = datetime.utcnow().isoformat()
        await asyncio.to_thread(doc_ref.set, data, True)
        return conv.conversation_id

    async def get_conversation(self, org_id: str, participant_id: str) -> Optional[dict]:
        ref = (
            self._collection(org_id)
            .where("participant_id", "==", participant_id)
            .order_by("last_message_at", direction="DESCENDING")
            .limit(1)
        )
        docs = await asyncio.to_thread(ref.get)
        if not docs:
            return None
        doc = docs[0]
        return {**doc.to_dict(), "id": doc.id}

    async def list_active_conversations(self, org_id: str, limit: int = 50) -> list[dict]:
        ref = (
            self._collection(org_id)
            .where("stage", "not-in", ["completed"])
            .order_by("last_message_at", direction="DESCENDING")
            .limit(limit)
        )
        docs = await asyncio.to_thread(ref.get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]

    async def list_needing_action(self, org_id: str) -> list[dict]:
        ref = (
            self._collection(org_id)
            .where("pending_action", "!=", "none")
            .order_by("last_message_at", direction="DESCENDING")
            .limit(50)
        )
        docs = await asyncio.to_thread(ref.get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]