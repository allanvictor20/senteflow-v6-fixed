"""
Tests for _daily_briefing_loop in main.py — covers:
- Correct argument types passed to operational_intelligence functions (Bug 1)
- Correct "trend" key used instead of "direction" (Bug 2)
- Sleep-after-work (first briefing fires immediately)
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call


def _make_repo(events=None, customers=None):
    repo = MagicMock()
    repo.list_transactions.return_value = events or []
    repo.list_customers.return_value = customers or []
    return repo


@pytest.mark.asyncio
async def test_briefing_calls_detect_functions_with_lists():
    """
    operational_intelligence functions expect list[dict], not (repo, org_id).
    The briefing loop must fetch data first, then pass lists to the functions.
    """
    from main import _daily_briefing_loop

    repo = _make_repo(events=[{"event_type": "payment_promise", "entities": {"amount": 50000}}])
    wa_client = AsyncMock()

    detect_calls = {}

    def fake_detect_overdue(events):
        assert isinstance(events, list), f"Expected list, got {type(events)}"
        detect_calls["overdue"] = True
        return []

    def fake_detect_lost(customers, days_threshold=45):
        assert isinstance(customers, list), f"Expected list, got {type(customers)}"
        detect_calls["lost"] = True
        return []

    def fake_detect_inv(events):
        assert isinstance(events, list), f"Expected list, got {type(events)}"
        detect_calls["inv"] = True
        return []

    def fake_detect_rev(events):
        assert isinstance(events, list), f"Expected list, got {type(events)}"
        detect_calls["rev"] = True
        return {"trend": "up", "change_percent": 12.5, "this_week": 1000, "last_week": 890}

    with patch("services.memory.operational_intelligence.detect_overdue_debts", side_effect=fake_detect_overdue), \
         patch("services.memory.operational_intelligence.detect_lost_customers", side_effect=fake_detect_lost), \
         patch("services.memory.operational_intelligence.detect_inventory_risk", side_effect=fake_detect_inv), \
         patch("services.memory.operational_intelligence.detect_revenue_trends", side_effect=fake_detect_rev), \
         patch.dict("os.environ", {"DEFAULT_OWNER_PHONE": "+256700000000"}):

        # Run one iteration (cancel after first sleep)
        task = asyncio.create_task(
            _daily_briefing_loop(wa_client, repo, "org-1", interval_hours=0.0001)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # All four detect functions should have been called
    assert "overdue" in detect_calls
    assert "lost" in detect_calls
    assert "inv" in detect_calls
    assert "rev" in detect_calls


@pytest.mark.asyncio
async def test_briefing_uses_trend_key_not_direction():
    """
    detect_revenue_trends() returns {"trend": "up"} — not {"direction": "up"}.
    The briefing loop must read the "trend" key.
    """
    from main import _daily_briefing_loop

    repo = _make_repo()
    wa_client = AsyncMock()

    trend_result = {"trend": "up", "change_percent": 20.0, "this_week": 1200, "last_week": 1000}
    # Note: no "direction" key — if the loop still reads "direction", the emoji won't appear

    with patch("services.memory.operational_intelligence.detect_overdue_debts", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_lost_customers", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_inventory_risk", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_revenue_trends", return_value=trend_result), \
         patch.dict("os.environ", {"DEFAULT_OWNER_PHONE": "+256700000000"}):

        task = asyncio.create_task(
            _daily_briefing_loop(wa_client, repo, "org-1", interval_hours=0.0001)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # wa_client.send_text should have been called with a message that contains revenue emoji
    wa_client.send_text.assert_called()
    sent_msg = wa_client.send_text.call_args[0][1]
    assert "📈" in sent_msg, f"Expected 📈 in briefing message but got: {sent_msg}"


@pytest.mark.asyncio
async def test_briefing_fires_immediately_not_after_sleep():
    """
    The loop should send the briefing on its FIRST iteration (before sleeping),
    not after a full 24h wait.
    """
    from main import _daily_briefing_loop

    repo = _make_repo(events=[
        {"event_type": "payment_promise", "entities": {"debtor": "Bob", "amount": 10000}, "created_at": "2026-01-01T00:00:00"}
    ])
    wa_client = AsyncMock()

    with patch("services.memory.operational_intelligence.detect_overdue_debts",
               return_value=[{"debtor": "Bob", "amount": 10000, "days_overdue": 3}]), \
         patch("services.memory.operational_intelligence.detect_lost_customers", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_inventory_risk", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_revenue_trends",
               return_value={"trend": "stable", "change_percent": 0}), \
         patch.dict("os.environ", {"DEFAULT_OWNER_PHONE": "+256700000000"}):

        # A very short interval — but we check the message was sent BEFORE the sleep ends
        task = asyncio.create_task(
            _daily_briefing_loop(wa_client, repo, "org-1", interval_hours=10)
        )
        # Give it a moment to run (not anywhere near 10h)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # The send should have happened in the first iteration, not after 10 hours
    wa_client.send_text.assert_called()
