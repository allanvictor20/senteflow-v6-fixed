"""Tests for Order domain model."""

import pytest
from domain.order import Order, OrderItem, OrderStatus, PaymentStatus


def make_order(**kwargs):
    defaults = {"org_id": "org-001", "customer_id": "cust-001", "customer_name": "John"}
    return Order(**{**defaults, **kwargs})


def test_order_defaults():
    o = make_order()
    assert o.status == OrderStatus.DRAFT
    assert o.payment_status == PaymentStatus.UNPAID
    assert o.total == 0.0


def test_compute_totals():
    o = make_order(items=[
        OrderItem(name="cement", quantity=20, unit_price=15_000, total_price=300_000),
    ])
    o.compute_totals()
    assert o.subtotal == 300_000
    assert o.total == 300_000
    assert o.debt_amount == 300_000


def test_record_full_payment():
    o = make_order(total=200_000, debt_amount=200_000)
    o.record_payment(200_000)
    assert o.payment_status == PaymentStatus.PAID
    assert o.status == OrderStatus.PAID
    assert o.debt_amount == 0.0


def test_record_partial_payment():
    o = make_order(total=200_000, debt_amount=200_000)
    o.record_payment(100_000)
    assert o.payment_status == PaymentStatus.PARTIAL
    assert o.debt_amount == 100_000


def test_timeline_grows_on_transitions():
    o = make_order()
    o.confirm()
    o.mark_awaiting_payment("Friday")
    assert len(o.timeline) == 2
    assert o.status == OrderStatus.AWAITING_PAYMENT


def test_cancel_order():
    o = make_order()
    o.cancel("Customer changed mind")
    assert o.status == OrderStatus.CANCELLED
    assert any("cancelled" in e.status for e in o.timeline)
