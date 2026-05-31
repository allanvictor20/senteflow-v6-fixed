"""
SenteFlow AI — Customer Repository
=====================================
Customers are first-class citizens with their own Firestore collection.
"""

import logging
from datetime import datetime
from typing import Optional
from domain.customers.model import Customer

logger = logging.getLogger(__name__)


class CustomerRepository:

    def __init__(self, db):
        self._db = db

    def _collection(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("customers")
        )

    def upsert_customer(self, org_id: str, customer: Customer) -> str:
        doc_ref = self._collection(org_id).document(customer.customer_id)
        data = customer.model_dump(mode="json")
        data["updated_at"] = datetime.utcnow().isoformat()
        doc_ref.set(data, merge=True)
        return customer.customer_id

    def get_customer(self, org_id: str, customer_id: str) -> Optional[dict]:
        doc = self._collection(org_id).document(customer_id).get()
        if not doc.exists:
            return None
        return {**doc.to_dict(), "id": doc.id}

    def list_customers(self, org_id: str, limit: int = 100) -> list[dict]:
        docs = self._collection(org_id).limit(limit).get()
        return [{**d.to_dict(), "id": d.id} for d in docs]

    def find_by_name(self, org_id: str, name: str) -> list[dict]:
        all_customers = self.list_customers(org_id)
        name_lower = name.lower().strip()
        return [
            c for c in all_customers
            if name_lower in c.get("display_name", "").lower()
            or any(name_lower in alias.lower() for alias in c.get("aliases", []))
        ]

    def update_balance(self, org_id: str, customer_id: str, amount_delta: float, direction: str) -> None:
        doc_ref = self._collection(org_id).document(customer_id)
        data = doc_ref.get().to_dict() or {}
        if direction == "paid":
            new_val = data.get("total_paid", 0.0) + amount_delta
            doc_ref.update({"total_paid": new_val, "updated_at": datetime.utcnow().isoformat()})
        elif direction == "owed":
            new_val = data.get("total_owed", 0.0) + amount_delta
            doc_ref.update({"total_owed": new_val, "updated_at": datetime.utcnow().isoformat()})