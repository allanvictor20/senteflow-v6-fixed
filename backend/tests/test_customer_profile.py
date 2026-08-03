"""Tests for CustomerProfile domain model."""

import pytest
from domain.customers.profile import CustomerProfile, PaymentBehavior, LoyaltyTier


def make_profile(**kwargs):
    defaults = {"id": "cust-001", "org_id": "org-001", "display_name": "Sarah"}
    return CustomerProfile(**{**defaults, **kwargs})


def test_profile_defaults():
    p = make_profile()
    assert p.total_orders == 0
    assert p.loyalty_tier == LoyaltyTier.NEW
    assert p.payment_behavior == PaymentBehavior.UNKNOWN


def test_order_event_increments_count():
    p = make_profile()
    p.update_from_event("customer_order", {"amount": 200_000, "item": "cement"})
    assert p.total_orders == 1
    assert p.total_spend == 200_000
    assert "cement" in p.preferred_products


def test_payment_event_reduces_outstanding():
    p = make_profile(total_outstanding=100_000)
    p.update_from_event("payment_received", {"amount": 60_000})
    assert p.total_paid == 60_000
    assert p.total_outstanding == 40_000


def test_loyalty_tier_upgrades():
    p = make_profile()
    for i in range(5):
        p.update_from_event("customer_order", {"amount": 100_000})
        p.update_from_event("payment_received", {"amount": 100_000})
    assert p.loyalty_tier in (LoyaltyTier.VIP, LoyaltyTier.CHAMPION)


def test_ai_context_string():
    p = make_profile(total_orders=3, total_spend=300_000)
    ctx = p.to_ai_context()
    assert "Sarah" in ctx
    assert "Orders: 3" in ctx


def test_payment_promise_adds_open_promise():
    p = make_profile()
    p.update_from_event("payment_promise", {"amount": 50_000, "due_date": "Friday"})
    assert len(p.open_promises) == 1
    assert p.open_promises[0]["due"] == "Friday"


def test_risk_score_increases_with_outstanding():
    p = make_profile(total_spend=200_000, total_paid=50_000)
    p.update_from_event("debt_created", {"amount": 100_000})
    assert p.risk_score > 0
