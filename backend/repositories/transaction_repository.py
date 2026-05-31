"""
SenteFlow AI — TransactionRepository (Compatibility Shim)
==========================================================
This class existed in earlier versions of SenteFlow and is still referenced
by several services. It now acts as a facade over the individual repositories.

New code should use the specific repositories directly:
  - EventRepository       → organizations/{org_id}/events
  - CustomerRepository    → organizations/{org_id}/customers
  - OrderRepository       → organizations/{org_id}/orders
  - MemoryRepository      → organizations/{org_id}/memory
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TransactionRepository:
    """
    Compatibility facade used by legacy services (action_dispatcher,
    message_router, reminder_sender, api/routes.py).

    All writes go to the canonical Firestore paths used by the new
    individual repositories so data stays consistent.
    """

    def __init__(self, db):
        self._db = db

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _org(self, org_id: str):
        return self._db.collection("organizations").document(org_id)

    def _col(self, org_id: str, name: str):
        return self._org(org_id).collection(name)

    # ── Business Events (replaces transactions) ───────────────────────────────

    def save_business_event(self, org_id: str, event: dict) -> str:
        event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
        event["event_id"] = event_id
        event.setdefault("created_at", datetime.utcnow().isoformat())
        self._col(org_id, "events").document(event_id).set(event, merge=True)
        logger.info("business_event_saved", extra={"event_id": event_id})
        return event_id

    def save_transaction(self, org_id: str, data: dict, sender_id: str = None) -> str:
        """Legacy alias — saves as a business event."""
        if sender_id:
            data["sender_id"] = sender_id
        return self.save_business_event(org_id, data)

    def get_transaction(self, org_id: str, txn_id: str) -> Optional[dict]:
        doc = self._col(org_id, "events").document(txn_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def list_transactions(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "events")
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_recent(self, org_id: str, limit: int = 50) -> list[dict]:
        return self.list_transactions(org_id, limit)

    def save_approved_batch(self, org_id: str, batch: list[dict]) -> list[str]:
        return [self.save_business_event(org_id, item) for item in batch]

    def compute_financial_summary(self, org_id: str) -> dict:
        events = self.list_transactions(org_id, limit=500)
        total_sales = sum(e.get("amount", 0) for e in events if e.get("event_type") == "sale")
        total_expenses = sum(e.get("amount", 0) for e in events if e.get("event_type") == "expense")
        total_received = sum(e.get("amount", 0) for e in events if e.get("event_type") == "payment_received")
        return {
            "total_sales": total_sales,
            "total_expenses": total_expenses,
            "total_received": total_received,
            "net": total_sales - total_expenses,
        }

    def update_ai_summary(self, org_id: str, txn_id: str, summary: str) -> None:
        self._col(org_id, "events").document(txn_id).update({
            "ai_summary": summary,
            "updated_at": datetime.utcnow().isoformat(),
        })

    # ── Customers ─────────────────────────────────────────────────────────────

    def get_customer(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "customers").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def get_by_phone(self, org_id: str, phone: str) -> Optional[dict]:
        docs = (
            self._col(org_id, "customers")
            .where("phone_number", "==", phone)
            .limit(1)
            .get()
        )
        for d in docs:
            return {**d.to_dict(), "id": d.id}
        return None

    def list_customers(self, org_id: str, limit: int = 100) -> list[dict]:
        docs = self._col(org_id, "customers").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def upsert_customer_profile(self, org_id: str, customer_id: str, updates: dict) -> None:
        updates["updated_at"] = datetime.utcnow().isoformat()
        self._col(org_id, "customers").document(customer_id).set(updates, merge=True)

    # ── Customer Profile ──────────────────────────────────────────────────────

    def get_customer_profile(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "customers").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def get_profile(self, org_id: str, customer_id: str) -> Optional[dict]:
        return self.get_customer_profile(org_id, customer_id)

    def upsert(self, org_id: str, customer_id: str, data: dict) -> None:
        self.upsert_customer_profile(org_id, customer_id, data)

    # ── Orders ────────────────────────────────────────────────────────────────

    def create_order(self, org_id: str, order_data: dict) -> str:
        order_id = order_data.get("id") or str(uuid.uuid4())
        order_data["id"] = order_id
        order_data.setdefault("created_at", datetime.utcnow().isoformat())
        self._col(org_id, "orders").document(order_id).set(order_data, merge=True)
        return order_id

    def list_orders(self, org_id: str, customer_id: str = None, limit: int = 50) -> list[dict]:
        ref = self._col(org_id, "orders")
        if customer_id:
            ref = ref.where("customer_id", "==", customer_id)
        docs = ref.order_by("created_at", direction="DESCENDING").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_active(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "orders")
            .where("status", "not-in", ["delivered", "cancelled", "completed"])
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    # ── Memory ────────────────────────────────────────────────────────────────

    def get_memory(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._col(org_id, "memory").document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def update_memory(self, org_id: str, customer_id: str, data: dict) -> None:
        data["updated_at"] = datetime.utcnow().isoformat()
        self._col(org_id, "memory").document(customer_id).set(data, merge=True)

    # ── Conversations ─────────────────────────────────────────────────────────

    def get_conversation(self, org_id: str, sender_id: str) -> Optional[dict]:
        doc = self._col(org_id, "conversations").document(sender_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def upsert_conversation(self, org_id: str, sender_id: str, data: dict) -> None:
        data["updated_at"] = datetime.utcnow().isoformat()
        self._col(org_id, "conversations").document(sender_id).set(data, merge=True)

    def append_conversation_timeline(self, org_id: str, sender_id: str, entry: dict) -> None:
        from google.cloud.firestore import ArrayUnion
        self._col(org_id, "conversations").document(sender_id).set(
            {"timeline": ArrayUnion([entry]), "updated_at": datetime.utcnow().isoformat()},
            merge=True,
        )

    def list_conversation_timeline(self, org_id: str, sender_id: str) -> list[dict]:
        data = self.get_conversation(org_id, sender_id)
        if not data:
            return []
        return data.get("timeline", [])

    def get_or_create(self, org_id: str, sender_id: str, name: str = None) -> dict:
        existing = self.get_conversation(org_id, sender_id)
        if existing:
            return existing
        new_conv = {
            "sender_id": sender_id,
            "display_name": name or sender_id,
            "created_at": datetime.utcnow().isoformat(),
            "timeline": [],
        }
        self.upsert_conversation(org_id, sender_id, new_conv)
        return new_conv

    def list(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "conversations")
            .order_by("updated_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    # ── Media Assets ──────────────────────────────────────────────────────────

    def save_media_asset(self, org_id: str, asset: dict) -> str:
        asset_id = asset.get("id") or str(uuid.uuid4())
        asset["id"] = asset_id
        self._col(org_id, "media_assets").document(asset_id).set(asset, merge=True)
        return asset_id

    def list_media_assets(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = self._col(org_id, "media_assets").limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    # ── Entity Links ──────────────────────────────────────────────────────────

    def save_entity_link(self, org_id: str, link: dict) -> str:
        link_id = link.get("id") or str(uuid.uuid4())
        link["id"] = link_id
        self._col(org_id, "entity_links").document(link_id).set(link, merge=True)
        return link_id

    # ── Webhook Queue ─────────────────────────────────────────────────────────

    def enqueue_webhook_event(self, org_id: str, event: dict) -> str:
        event_id = str(uuid.uuid4())
        event["id"] = event_id
        event["status"] = "queued"
        event["created_at"] = datetime.utcnow().isoformat()
        self._col(org_id, "webhook_queue").document(event_id).set(event)
        return event_id

    def get_queued_webhook_event(self, org_id: str) -> Optional[dict]:
        docs = (
            self._col(org_id, "webhook_queue")
            .where("status", "==", "queued")
            .order_by("created_at")
            .limit(1)
            .get()
        )
        for d in docs:
            return {**d.to_dict(), "id": d.id}
        return None

    def mark_webhook_processing(self, org_id: str, event_id: str) -> None:
        self._col(org_id, "webhook_queue").document(event_id).update({"status": "processing"})

    def mark_webhook_completed(self, org_id: str, event_id: str) -> None:
        self._col(org_id, "webhook_queue").document(event_id).update({"status": "completed"})

    def mark_webhook_failed(self, org_id: str, event_id: str, error: str = "") -> None:
        self._col(org_id, "webhook_queue").document(event_id).update({
            "status": "failed",
            "error": error,
        })

    # ── Debts / Overdue ───────────────────────────────────────────────────────

    def list_overdue(self, org_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "events")
            .where("event_type", "in", ["payment_promised", "debt_created"])
            .where("resolved", "==", False)
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_by_customer(self, org_id: str, customer_id: str, limit: int = 50) -> list[dict]:
        docs = (
            self._col(org_id, "events")
            .where("sender_id", "==", customer_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def list_requiring_followup(self, org_id: str, limit: int = 50) -> list[dict]:
        return self.list_overdue(org_id, limit)

    def complete_task(self, org_id: str, task_id: str) -> None:
        self._col(org_id, "tasks").document(task_id).update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(),
        })
