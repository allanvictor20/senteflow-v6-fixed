"""
SenteFlow AI — Order Repository
=================================
Firestore persistence for Orders.
"""

import logging
from datetime import datetime
from typing import Optional

from domain.orders.model import Order, OrderStatus

logger = logging.getLogger(__name__)


class OrderRepository:

    def __init__(self, db):
        self._db = db

    def _col(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("orders")
        )

    def save(self, org_id: str, order: Order) -> str:
        order.org_id = org_id
        order.updated_at = datetime.utcnow().isoformat()
        self._col(org_id).document(order.id).set(order.model_dump(mode="json"), merge=True)
        logger.debug("order_saved", extra={"order_id": order.id, "status": order.status})
        return order.id

    def get(self, org_id: str, order_id: str) -> Optional[Order]:
        doc = self._col(org_id).document(order_id).get()
        if not doc.exists:
            return None
        return Order(**doc.to_dict())

    def list_by_customer(self, org_id: str, customer_id: str, limit: int = 50) -> list[Order]:
        docs = (
            self._col(org_id)
            .where("customer_id", "==", customer_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        orders = []
        for d in docs:
            try:
                orders.append(Order(**d.to_dict()))
            except Exception as e:
                logger.warning("order_parse_error", extra={"id": d.id, "error": str(e)})
        return orders

    def list_by_status(self, org_id: str, status: OrderStatus, limit: int = 100) -> list[Order]:
        docs = (
            self._col(org_id)
            .where("status", "==", status.value)
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [Order(**d.to_dict()) for d in docs]

    def list_recent(self, org_id: str, limit: int = 50) -> list[Order]:
        docs = (
            self._col(org_id)
            .order_by("updated_at", direction="DESCENDING")
            .limit(limit)
            .get()
        )
        return [Order(**d.to_dict()) for d in docs]

    def update_status(self, org_id: str, order_id: str, status: OrderStatus) -> None:
        self._col(org_id).document(order_id).update({
            "status": status.value,
            "updated_at": datetime.utcnow().isoformat(),
        })

    def get_active_for_customer(self, org_id: str, customer_id: str) -> Optional[Order]:
        """Get the most recent non-completed, non-cancelled order for a customer."""
        terminal = {OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value, OrderStatus.COMPLETED.value if hasattr(OrderStatus, 'COMPLETED') else None}
        docs = (
            self._col(org_id)
            .where("customer_id", "==", customer_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(10)
            .get()
        )
        for d in docs:
            data = d.to_dict()
            if data.get("status") not in terminal:
                try:
                    return Order(**data)
                except Exception:
                    pass
        return None