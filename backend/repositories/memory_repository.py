"""Stores and retrieves CustomerMemory objects."""

import asyncio


from typing import Optional

from domain.business_memory.model import CustomerMemory
from utils.clock import utc_now


class MemoryRepository:
    def __init__(self, db):
        self._db = db

    def _collection(self, org_id: str):
        return self._db.collection("organizations").document(org_id).collection("memory")

    async def update_memory(self, org_id: str, memory: CustomerMemory) -> str:
        doc_ref = self._collection(org_id).document(memory.customer_id)
        data = memory.model_dump(mode="json")
        data["updated_at"] = utc_now().isoformat()
        await asyncio.to_thread(doc_ref.set, data, True)
        return memory.customer_id

    async def get_memory(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = await asyncio.to_thread(self._collection(org_id).document(customer_id).get)
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    async def get_memory_context(self, org_id: str, customer_id: str) -> str:
        mem = await self.get_memory(org_id, customer_id)
        if not mem:
            return ""
        try:
            return CustomerMemory(**mem).to_ai_context()
        except Exception:
            return ""

    async def list_high_risk(self, org_id: str) -> list[dict]:
        ref = (
            self._collection(org_id)
            .where("risk_level", "==", "high")
            .order_by("total_outstanding", direction="DESCENDING")
            .limit(50)
        )
        docs = await asyncio.to_thread(ref.get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]

    async def list_top_customers(self, org_id: str, limit: int = 10) -> list[dict]:
        ref = self._collection(org_id).order_by("relationship_score", direction="DESCENDING").limit(limit)
        docs = await asyncio.to_thread(ref.get)
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]
