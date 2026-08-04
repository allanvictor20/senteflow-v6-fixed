"""
Tests for ActionDispatcher (v5) — ToolBase API, due-date resolution,
permission system, clarification flow, and dispatch routing.

v5 change: actions are ToolBase subclasses, not free functions.
All tests use the public dispatch() API or the tool classes directly.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import timedelta

from domain.events.business_event import BusinessEvent
from domain.events.event_types import EventType
from services.actions.action_dispatcher import (
    _resolve_due_date,
    UpdateLedgerTool,
    ScheduleReminderTool,
    ActionDispatcher,
    ToolResult,
)
from utils.clock import utc_now


# ── Shared test double ────────────────────────────────────────────────────────

class FakeRepo:
    def __init__(self):
        self.saved_transactions = []
        self.saved_events = []
        self._db = MagicMock()

    def save_transaction(self, org_id, txn_dict, recorded_by):
        self.saved_transactions.append(txn_dict)
        return "fake-txn-id"

    def save_business_event(self, org_id, event_dict):
        self.saved_events.append(event_dict)
        return "fake-event-id"

    def upsert_customer_profile(self, org_id, sender_id, updates): return sender_id
    def upsert_conversation(self, org_id, sender_id, updates):      return sender_id
    def append_conversation_timeline(self, org_id, sender_id, e):   return "tid"
    def list_transactions(self, org_id, limit=100, **kw):           return []
    def get_conversation(self, org_id, sender_id):                  return None
    def get_customer_profile(self, org_id, sender_id):              return None
    def list_orders(self, org_id, limit=50):                        return []


# ── _resolve_due_date (unchanged helper) ──────────────────────────────────────

class TestResolveDueDate:
    def test_none(self):         assert _resolve_due_date(None) == "soon"
    def test_empty(self):        assert _resolve_due_date("") == "soon"
    def test_day_name(self):     assert _resolve_due_date("friday") == "Friday"
    def test_explicit_day(self): assert _resolve_due_date("Monday") == "Monday"

    def test_tomorrow(self):
        expected = (utc_now() + timedelta(days=1)).strftime("%a %d %b")
        assert _resolve_due_date("tomorrow") == expected

    def test_iso_date(self):
        assert _resolve_due_date("2025-06-03") == "Tue 03 Jun"


# ── UpdateLedgerTool ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_ledger_returns_clarification_when_no_amount():
    """Missing required entity → ask_clarification=True, not a failure."""
    event = BusinessEvent(event_type=EventType.PAYMENT_RECEIVED, sender_id="s1", entities={})
    result = await UpdateLedgerTool().execute(event, FakeRepo(), "org-1")
    assert isinstance(result, ToolResult)
    assert result.ask_clarification is True
    assert result.clarification_question  # non-empty


@pytest.mark.asyncio
async def test_update_ledger_saves_transaction_when_amount_present():
    event = BusinessEvent(
        event_type=EventType.PAYMENT_RECEIVED,
        sender_id="s1",
        entities={"amount": 50000.0, "payer": "Sarah", "currency": "UGX"},
    )
    repo = FakeRepo()
    result = await UpdateLedgerTool().execute(event, repo, "org-1")
    assert result.outcome.startswith("update_ledger:ok")
    assert result.ask_clarification is False
    assert len(repo.saved_transactions) == 1
    assert repo.saved_transactions[0]["amount"] == 50000.0


# ── ScheduleReminderTool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_reminder_clarifies_when_debtor_missing():
    """debtor is a required entity — missing → clarification, not error."""
    event = BusinessEvent(
        event_type=EventType.PAYMENT_PROMISE,
        sender_id="s1",
        entities={"amount": 70000.0, "due_date": "Friday"},  # no debtor
    )
    result = await ScheduleReminderTool().execute(event, FakeRepo(), "org-1")
    assert result.ask_clarification is True
    assert "remind" in result.clarification_question.lower()


@pytest.mark.asyncio
async def test_schedule_reminder_sets_due_display():
    event = BusinessEvent(
        event_type=EventType.PAYMENT_PROMISE,
        sender_id="s1",
        entities={"debtor": "Brian", "amount": 70000.0, "due_date": "Friday"},
    )
    with patch("services.actions.action_dispatcher.BusinessMemoryEngine") as M:
        M.return_value.remember_event = MagicMock()
        result = await ScheduleReminderTool().execute(event, FakeRepo(), "org-1")
    assert result.outcome.startswith("schedule_reminder:ok")
    assert event.entities.get("_due_display") == "Friday"


# ── ActionDispatcher end-to-end ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatcher_executes_ledger_for_payment():
    event = BusinessEvent(
        event_type=EventType.PAYMENT_RECEIVED,
        sender_id="s1",
        entities={"amount": 50000.0, "payer": "Alice", "currency": "UGX"},
        recommended_actions=["update_ledger"],
    )
    result = await ActionDispatcher(repo=FakeRepo(), org_id="org-1").dispatch(event)
    assert result.success
    assert any("update_ledger:ok" in a for a in result.actions_executed)


@pytest.mark.asyncio
async def test_dispatcher_unknown_action_goes_to_failed():
    event = BusinessEvent(
        event_type=EventType.UNKNOWN,
        sender_id="s1",
        entities={},
        recommended_actions=["nonexistent_action"],
    )
    result = await ActionDispatcher(repo=FakeRepo(), org_id="org-1").dispatch(event)
    assert any("nonexistent_action:unknown" in a for a in result.actions_failed)


@pytest.mark.asyncio
async def test_dispatcher_returns_clarification_reply_on_missing_entities():
    """Dispatcher surfaces clarification question as whatsapp_reply."""
    event = BusinessEvent(
        event_type=EventType.PAYMENT_PROMISE,
        sender_id="s1",
        entities={},                          # debtor missing → clarification
        recommended_actions=["schedule_reminder"],
    )
    result = await ActionDispatcher(repo=FakeRepo(), org_id="org-1").dispatch(event)
    assert result.success  # clarification is not a failure
    assert result.whatsapp_reply  # question sent to user


@pytest.mark.asyncio
async def test_dispatcher_permission_blocks_reduce_debt():
    """reduce_debt requires approval by default — dispatcher should pause."""
    from domain.permissions.model import OrgConfig
    event = BusinessEvent(
        event_type=EventType.PAYMENT_RECEIVED,
        sender_id="s1",
        entities={"amount": 50000.0},
        recommended_actions=["reduce_debt"],
    )
    result = await ActionDispatcher(
        repo=FakeRepo(), org_id="org-1", org_config=OrgConfig.default("org-1")
    ).dispatch(event)
    # reduce_debt is APPROVAL_REQUIRED → pending_approval set
    assert result.pending_approval or any("pending_approval" in a for a in result.actions_executed)


@pytest.mark.asyncio
async def test_dispatcher_permission_blocks_large_ledger():
    """update_ledger > 500k threshold triggers approval flow."""
    from domain.permissions.model import OrgConfig
    event = BusinessEvent(
        event_type=EventType.PAYMENT_RECEIVED,
        sender_id="s1",
        entities={"amount": 600_000.0, "payer": "Rich Client", "currency": "UGX"},
        recommended_actions=["update_ledger"],
    )
    result = await ActionDispatcher(
        repo=FakeRepo(), org_id="org-1", org_config=OrgConfig.default("org-1")
    ).dispatch(event)
    assert result.pending_approval or any("pending_approval" in a for a in result.actions_executed)
