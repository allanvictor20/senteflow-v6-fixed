"""Tests for TaskGenerationService."""

import pytest
from unittest.mock import MagicMock
from domain.events.business_event import BusinessEvent
from domain.events.event_types import EventType
from services.tasks.task_generation_service import TaskGenerationService
from domain.debts.task import TaskCategory, TaskPriority


def make_event(event_type: str, entities: dict = None, sender_id: str = "cust-001") -> BusinessEvent:
    return BusinessEvent(
        event_type=EventType(event_type),
        sender_id=sender_id,
        business_id="org-001",
        raw_message="test message",
        entities=entities or {},
    )


def make_svc():
    repo = MagicMock()
    repo.save = MagicMock(return_value="task-001")
    return TaskGenerationService(task_repo=repo), repo


def test_payment_promise_generates_followup():
    svc, repo = make_svc()
    event = make_event("payment_promise", {"amount": 50_000, "due_date": "Friday", "payer": "Sarah"})
    tasks = svc.generate_from_event("org-001", event)
    assert len(tasks) == 1
    assert tasks[0].category == TaskCategory.PAYMENT_COLLECTION
    assert "Sarah" in tasks[0].title or "cust-001" in tasks[0].title
    assert repo.save.called


def test_low_stock_generates_restock():
    svc, repo = make_svc()
    event = make_event("low_stock_alert", {"item": "sugar"})
    tasks = svc.generate_from_event("org-001", event)
    assert len(tasks) == 1
    assert "sugar" in tasks[0].title
    assert tasks[0].category == TaskCategory.INVENTORY


def test_customer_order_generates_confirm_task():
    svc, repo = make_svc()
    event = make_event("customer_order", {"item": "cement", "quantity": 20})
    tasks = svc.generate_from_event("org-001", event)
    assert len(tasks) == 1
    assert tasks[0].category == TaskCategory.CUSTOMER_SERVICE


def test_complaint_generates_urgent_task():
    svc, repo = make_svc()
    event = make_event("complaint", {})
    tasks = svc.generate_from_event("org-001", event)
    assert len(tasks) == 1
    assert tasks[0].priority == TaskPriority.URGENT


def test_payment_received_with_order_id_generates_delivery():
    svc, repo = make_svc()
    event = make_event("payment_received", {"amount": 100_000})
    tasks = svc.generate_from_event("org-001", event, order_id="order-abc")
    assert len(tasks) == 1
    assert tasks[0].category == TaskCategory.DELIVERY


def test_unknown_event_generates_no_tasks():
    svc, repo = make_svc()
    event = make_event("unknown", {})
    tasks = svc.generate_from_event("org-001", event)
    assert tasks == []
