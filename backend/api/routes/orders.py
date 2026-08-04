"""
SenteFlow AI — Order Routes
=============================
GET  /api/orders                  — list recent orders
GET  /api/orders/{order_id}       — get one order
POST /api/orders/{order_id}/confirm
POST /api/orders/{order_id}/cancel
GET  /api/orders/customer/{customer_id}
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import verify_firebase_token, ensure_org_access

logger = logging.getLogger(__name__)

orders_router = APIRouter(prefix="/api/orders", tags=["orders"])

_order_repo = None
_order_svc = None


def set_order_dependencies(order_repo, order_svc):
    global _order_repo, _order_svc
    _order_repo = order_repo
    _order_svc = order_svc


@orders_router.get("")
async def list_orders(
    org_id: str,
    status: str = None,
    limit: int = 50,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    if not _order_repo:
        raise HTTPException(500, "Order repository not initialised")

    if status:
        from domain.orders.model import OrderStatus
        try:
            s = OrderStatus(status)
        except ValueError:
            raise HTTPException(400, f"Unknown status: {status}")
        orders = _order_repo.list_by_status(org_id, s, limit=limit)
    else:
        orders = _order_repo.list_recent(org_id, limit=limit)

    return {"orders": [o.model_dump(mode="json") for o in orders]}


@orders_router.get("/customer/{customer_id}")
async def list_customer_orders(
    customer_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    orders = _order_repo.list_by_customer(org_id, customer_id)
    return {"orders": [o.model_dump(mode="json") for o in orders]}


@orders_router.get("/{order_id}")
async def get_order(
    order_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    order = _order_repo.get(org_id, order_id)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    return order.model_dump(mode="json")


@orders_router.post("/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    order = _order_svc.confirm_order(org_id, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    return order.model_dump(mode="json")


class CancelRequest(BaseModel):
    reason: str = ""


@orders_router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    org_id: str,
    body: CancelRequest,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    order = _order_svc.cancel_order(org_id, order_id, body.reason)
    if not order:
        raise HTTPException(404, "Order not found")
    return order.model_dump(mode="json")