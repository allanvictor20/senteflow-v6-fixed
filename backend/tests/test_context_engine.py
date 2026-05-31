"""
Tests for ContextEngine — key assembly, partial Firestore failure resilience, risk scoring.
"""
import pytest
from services.memory.context_engine import ContextEngine


class FakeRepo:
    def __init__(self, raise_on=None):
        self._raise = raise_on or set()

    def get_conversation(self, org_id, sender_id):
        if "conversation" in self._raise: raise RuntimeError("db down")
        return {"state": "pending_inquiry"}

    def list_conversation_timeline(self, org_id, sender_id, limit=10):
        if "timeline" in self._raise: raise RuntimeError("db down")
        return []

    def list_orders(self, org_id, limit=50):
        if "orders" in self._raise: raise RuntimeError("db down")
        return []

    def find_entity_links(self, org_id, entity_type, entity_id, limit=25):
        if "links" in self._raise: raise RuntimeError("db down")
        return []

    def list_transactions(self, org_id, limit=100, **kw):
        return [{"recorded_by": "s1", "amount": 50000, "status": "approved"}]

    def get_customer_profile(self, org_id, sender_id):
        return {"display_name": "Alice", "outstanding_balance": 30000}

    def compute_financial_summary(self, org_id):
        from domain.models import FinancialSummary  # domain/models.py shim
        return FinancialSummary(total_income=500000, total_expenses=200000, balance=300000)


@pytest.mark.asyncio
async def test_context_has_required_keys():
    ctx = await ContextEngine(repo=FakeRepo(), org_id="org-1").get_context("s1", "Brian paid 50k")
    for key in ("customer", "conversation", "open_orders", "entity_links"):
        assert key in ctx


@pytest.mark.asyncio
async def test_survives_conversation_failure():
    ctx = await ContextEngine(repo=FakeRepo(raise_on={"conversation"}), org_id="org-1").get_context("s1", "")
    assert ctx["conversation"] == {}
    assert "open_orders" in ctx


@pytest.mark.asyncio
async def test_survives_orders_failure():
    ctx = await ContextEngine(repo=FakeRepo(raise_on={"orders"}), org_id="org-1").get_context("s1", "")
    assert ctx["open_orders"] == []
    assert "conversation" in ctx


class TestRiskScoring:
    def _engine(self, balance):
        class R(FakeRepo):
            def get_customer_profile(self, org_id, sender_id):
                return {"outstanding_balance": balance}
        return ContextEngine(repo=R(), org_id="org-1")

    @pytest.mark.asyncio
    async def test_high(self):
        ctx = await self._engine(250000)._customer_context("s1")
        assert ctx["risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_medium(self):
        ctx = await self._engine(100000)._customer_context("s1")
        assert ctx["risk_level"] == "medium"

    @pytest.mark.asyncio
    async def test_low(self):
        ctx = await self._engine(10000)._customer_context("s1")
        assert ctx["risk_level"] == "low"