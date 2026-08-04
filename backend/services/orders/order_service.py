"""
SenteFlow AI — OrderService
=============================
Order lifecycle transitions, kept out of the HTTP layer.

The routes in api/routes/orders.py were written against this service but it had
never been implemented, so every confirm/cancel call hit `None`. The state
transitions themselves live on the Order model — this service is the thin
load → transition → save wrapper around them.
"""

import logging
from typing import Optional

from domain.orders.model import Order, OrderStatus

logger = logging.getLogger(__name__)


class OrderService:

    def __init__(self, order_repo):
        self._repo = order_repo

    def confirm_order(self, org_id: str, order_id: str) -> Optional[Order]:
        order = self._repo.get(org_id, order_id)
        if not order:
            return None
        if order.status == OrderStatus.CANCELLED:
            logger.info("confirm_skipped_cancelled_order", extra={"order_id": order_id})
            return order
        order.confirm()
        self._repo.save(org_id, order)
        logger.info("order_confirmed", extra={"order_id": order_id, "org_id": org_id})
        return order

    def cancel_order(self, org_id: str, order_id: str, reason: str = "") -> Optional[Order]:
        order = self._repo.get(org_id, order_id)
        if not order:
            return None
        if order.status == OrderStatus.DELIVERED:
            logger.info("cancel_skipped_delivered_order", extra={"order_id": order_id})
            return order
        order.cancel(reason)
        self._repo.save(org_id, order)
        logger.info("order_cancelled", extra={"order_id": order_id, "reason": reason})
        return order

    def record_payment(self, org_id: str, order_id: str, amount: float) -> Optional[Order]:
        order = self._repo.get(org_id, order_id)
        if not order:
            return None
        order.record_payment(amount)
        self._repo.save(org_id, order)
        return order

    def mark_delivered(self, org_id: str, order_id: str) -> Optional[Order]:
        order = self._repo.get(org_id, order_id)
        if not order:
            return None
        order.mark_delivered()
        self._repo.save(org_id, order)
        return order
