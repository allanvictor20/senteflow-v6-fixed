"""
Tests for the morning briefing (tasks/briefing.py):
- the detect_* functions receive lists, not (repo, org_id)
- the revenue line reads the "trend" key
- sent once per business per day, after BRIEFING_HOUR, to the owners' numbers
- a failed send releases the day's claim so it is retried
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tasks import briefing


def _make_repo(events=None, customers=None, promises=None):
    repo = MagicMock()
    repo.list_transactions.return_value = events or []
    repo.list_customers.return_value = customers or []
    repo.list_open_promises.return_value = promises or []
    return repo


class FakeTenants:
    def __init__(self, phones=("256700000000",)):
        self._phones = list(phones)

    def owner_phones(self, org_id):
        return self._phones

    def list_org_ids(self):
        return ["org-1"]


def test_build_briefing_passes_lists_to_detectors():
    seen = {}

    def _rec(name, ret):
        def fn(data, *a, **k):
            assert isinstance(data, list), f"{name} got {type(data)}"
            seen[name] = True
            return ret
        return fn

    with patch("services.memory.operational_intelligence.detect_overdue_debts", side_effect=_rec("overdue", [])), \
         patch("services.memory.operational_intelligence.detect_lost_customers", side_effect=_rec("lost", [])), \
         patch("services.memory.operational_intelligence.detect_inventory_risk", side_effect=_rec("inv", [])), \
         patch("services.memory.operational_intelligence.detect_revenue_trends",
               side_effect=_rec("rev", {"trend": "up", "change_percent": 12.5})):
        msg = briefing.build_briefing(_make_repo(), "org-1")

    assert seen == {"overdue": True, "lost": True, "inv": True, "rev": True}
    assert msg and "📈" in msg


def test_build_briefing_uses_trend_key():
    with patch("services.memory.operational_intelligence.detect_overdue_debts", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_lost_customers", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_inventory_risk", return_value=[]), \
         patch("services.memory.operational_intelligence.detect_revenue_trends",
               return_value={"trend": "down", "change_percent": -20.0}):
        msg = briefing.build_briefing(_make_repo(), "org-1")
    assert "📉" in msg and "20.0%" in msg


def test_build_briefing_none_when_nothing_to_report():
    with patch("services.memory.operational_intelligence.detect_revenue_trends",
               return_value={"trend": "stable", "change_percent": 0}):
        assert briefing.build_briefing(_make_repo(), "org-1") is None


def test_overdue_promise_with_due_at_is_reported():
    promises = [{
        "type": "payment_promise",
        "status": "pending",
        "due_at": "2020-01-01T20:00:00",
        "entities": {"customer": "Bob", "amount": 10000},
    }]
    msg = briefing.build_briefing(_make_repo(promises=promises), "org-1")
    assert msg and "Bob" in msg and "10,000" in msg


@pytest.mark.asyncio
async def test_not_sent_before_briefing_hour():
    wa = AsyncMock()
    early = datetime(2026, 11, 5, 5, 0)
    with patch.dict("os.environ", {"BRIEFING_HOUR": "7"}):
        sent = await briefing.send_briefing_for_org(wa, _make_repo(), FakeTenants(), "org-1", now=early)
    assert sent is False
    wa.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_sent_once_to_each_owner_after_hour():
    wa = AsyncMock()
    wa.send_text.return_value = {"key": {"id": "x"}}
    now = datetime(2026, 11, 5, 8, 0)
    with patch.object(briefing, "claim_briefing", side_effect=[True, False]), \
         patch.object(briefing, "build_briefing", return_value="Good morning!"), \
         patch.dict("os.environ", {"BRIEFING_HOUR": "7"}):
        first = await briefing.send_briefing_for_org(wa, _make_repo(), FakeTenants(["256700000001", "256700000002"]), "org-1", now=now)
        second = await briefing.send_briefing_for_org(wa, _make_repo(), FakeTenants(["256700000001", "256700000002"]), "org-1", now=now)
    assert first is True and second is False
    assert wa.send_text.call_count == 2
    assert wa.send_text.call_args_list[0][0][0] == "256700000001@s.whatsapp.net"


@pytest.mark.asyncio
async def test_failed_send_releases_claim():
    wa = AsyncMock()
    wa.send_text.return_value = {"error": "connection refused"}
    now = datetime(2026, 11, 5, 8, 0)
    with patch.object(briefing, "claim_briefing", return_value=True), \
         patch.object(briefing, "build_briefing", return_value="Good morning!"), \
         patch.object(briefing, "release_briefing") as release, \
         patch.dict("os.environ", {"BRIEFING_HOUR": "7"}):
        sent = await briefing.send_briefing_for_org(wa, _make_repo(), FakeTenants(), "org-1", now=now)
    assert sent is False
    release.assert_called_once()
    assert release.call_args[0][1:] == ("org-1", "2026-11-05")
