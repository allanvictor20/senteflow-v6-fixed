"""Tests for ConversationAggregate state machine."""

import pytest
from domain.conversations.state import ConversationAggregate, ConversationStatus


def make_agg(**kwargs):
    defaults = {"org_id": "org-001", "customer_id": "cust-001"}
    return ConversationAggregate(**{**defaults, **kwargs})


def test_initial_state():
    agg = make_agg()
    assert agg.current_state == ConversationStatus.NEW


def test_inquiry_transition():
    agg = make_agg()
    new_state = agg.apply_event("customer_inquiry", "evt-001", {})
    assert new_state == ConversationStatus.INQUIRY
    assert len(agg.state_history) == 1


def test_order_to_awaiting_payment():
    agg = make_agg()
    agg.apply_event("customer_order", "evt-001", {})
    agg.apply_event("payment_promise", "evt-002", {"due_date": "tomorrow"})
    assert agg.current_state == ConversationStatus.AWAITING_PAYMENT


def test_full_happy_path():
    agg = make_agg()
    steps = [
        ("customer_inquiry", {}),
        ("customer_order", {"amount": 100_000}),
        ("payment_promise", {"due_date": "Friday"}),
        ("payment_received", {"amount": 100_000}),
    ]
    for event_type, entities in steps:
        agg.apply_event(event_type, f"evt-{event_type}", entities)

    assert agg.current_state == ConversationStatus.PAYMENT_RECEIVED
    assert len(agg.state_history) == 4


def test_event_ids_accumulated():
    agg = make_agg()
    agg.apply_event("customer_inquiry", "evt-001", {})
    agg.apply_event("customer_order", "evt-002", {})
    assert "evt-001" in agg.event_ids
    assert "evt-002" in agg.event_ids


def test_expected_next_action_set():
    agg = make_agg()
    agg.apply_event("payment_promise", "evt-001", {"due_date": "Monday"})
    assert "Collect payment" in agg.expected_next_action


def test_unknown_event_type_no_crash():
    agg = make_agg()
    result = agg.apply_event("something_weird", "evt-001", {})
    assert result is None
    assert agg.current_state == ConversationStatus.NEW
