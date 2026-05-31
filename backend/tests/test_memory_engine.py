"""
Tests for BusinessMemoryEngine — promise storage, retrieval, resolution, pattern detection.
All Firestore calls replaced with FakeRepo / FakeFirestore stubs.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timedelta
from services.memory.memory_engine import BusinessMemoryEngine
from domain.events.event_types import EventType
from domain.events.business_event import BusinessEvent


class FakeCollection:
    """Minimal in-memory Firestore collection stub."""
    def __init__(self):
        self._docs = {}

    def document(self, doc_id=None):
        doc_id = doc_id or f"doc-{len(self._docs)}"
        stub = MagicMock()
        stub.id = doc_id
        stub.set = lambda data: self._docs.update({doc_id: data})
        stub.update = lambda data: self._docs[doc_id].update(data) if doc_id in self._docs else None
        return stub

    def where(self, *args, **kwargs):
        filtered = FakeCollection()
        filtered._docs = {k: v for k, v in self._docs.items() if self._matches(v, args)}
        return filtered

    def _matches(self, doc, args):
        if len(args) == 3:
            field, op, value = args
            return op == "==" and doc.get(field) == value
        return True

    def get(self):
        results = []
        for doc_id, data in self._docs.items():
            m = MagicMock()
            m.to_dict.return_value = data
            results.append(m)
        return results


class FakeDB:
    def __init__(self):
        self._collections = {}

    def collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


class FakeRepo:
    def __init__(self):
        self._db = FakeDB()
        self._transactions = []

    async def list_transactions(self, org_id, limit=200):
        return self._transactions


def make_promise_event(sender="s1", amount=50000.0, due="Friday"):
    return BusinessEvent(
        event_type=EventType.PAYMENT_PROMISE,
        sender_id=sender,
        entities={"amount": amount, "debtor": "Brian", "due_date": due},
        raw_message="He will pay Friday",
    )


class TestBusinessMemoryEngine:
    def _engine(self):
        repo = FakeRepo()
        return BusinessMemoryEngine(repo, "org-1"), repo

    def test_remember_promise_event_does_not_raise(self):
        engine, _ = self._engine()
        event = make_promise_event()
        engine.remember_event(event)  # should not raise

    def test_get_unresolved_promises_returns_list(self):
        engine, _ = self._engine()
        event = make_promise_event()
        engine._store_promise(event)
        promises = engine.get_unresolved_promises()
        assert isinstance(promises, list)

    def test_resolve_promise_marks_resolved(self):
        engine, repo = self._engine()
        event = make_promise_event()
        engine._store_promise(event)
        result = engine.resolve_promise(event.event_id)
        assert result is True

    def test_get_overdue_promises_filters_old(self):
        engine, _ = self._engine()
        old_event = make_promise_event()
        # Manually inject an old promise
        old_doc = {
            "type": "payment_promise",
            "event_id": old_event.event_id,
            "entities": {},
            "sender_id": "s1",
            "created_at": (datetime.utcnow() - timedelta(days=5)).isoformat(),
            "status": "pending",
            "org_id": "org-1",
        }
        engine.repo._db.collection("organizations").document("org-1")  # init
        # Patch get_unresolved_promises to return our controlled data
        engine.get_unresolved_promises = lambda: [old_doc]
        overdue = engine.get_overdue_promises(days_threshold=2)
        assert len(overdue) == 1

    def test_get_overdue_promises_excludes_recent(self):
        engine, _ = self._engine()
        recent_doc = {
            "created_at": datetime.utcnow().isoformat(),
            "status": "pending",
        }
        engine.get_unresolved_promises = lambda: [recent_doc]
        overdue = engine.get_overdue_promises(days_threshold=2)
        assert len(overdue) == 0

    @pytest.mark.asyncio
    async def test_get_customer_memory_no_transactions(self):
        engine, _ = self._engine()
        memory = await engine.get_customer_memory("s-unknown")
        assert memory["outstanding_balance"] == 0.0
        assert memory["total_transactions"] == 0

    @pytest.mark.asyncio
    async def test_find_patterns_empty(self):
        engine, _ = self._engine()
        patterns = await engine.find_patterns()
        assert "top_categories" in patterns
        assert patterns["total_transactions_analysed"] == 0

# ── Tests for new methods added in v6 fix ─────────────────────────────────────

class TestSurfaceProactiveInsights:
    """surface_proactive_insights() must never raise and returns a list."""

    def _engine(self):
        repo = FakeRepo()
        return BusinessMemoryEngine(repo, "org-1"), repo

    def test_returns_empty_list_for_new_customer(self):
        engine, _ = self._engine()
        insights = engine.surface_proactive_insights("unknown-sender")
        assert isinstance(insights, list)
        assert insights == []

    def test_returns_list_when_overdue_promise_exists(self):
        engine, repo = self._engine()
        # Store a promise directly
        engine._store_promise(make_promise_event(sender="s1"))
        insights = engine.surface_proactive_insights("s1")
        # May be empty if the promise is not yet 'overdue' (< 2 days),
        # but must always be a list and never raise.
        assert isinstance(insights, list)

    def test_never_raises_on_db_failure(self):
        """Even if Firestore is down, must return []."""
        from unittest.mock import patch
        engine, _ = self._engine()
        with patch.object(engine, "get_unresolved_promises", side_effect=RuntimeError("db down")):
            result = engine.surface_proactive_insights("s1")
        assert isinstance(result, list)


class TestGetPaymentPatterns:
    """get_payment_patterns() must never raise and returns a dict."""

    def _engine(self):
        repo = FakeRepo()
        return BusinessMemoryEngine(repo, "org-1"), repo

    def test_returns_dict(self):
        engine, _ = self._engine()
        patterns = engine.get_payment_patterns("s1")
        assert isinstance(patterns, dict)
        assert "reliability" in patterns

    def test_unknown_for_no_data(self):
        engine, _ = self._engine()
        patterns = engine.get_payment_patterns("new-customer")
        assert patterns["reliability"] == "unknown"

    def test_never_raises_on_db_failure(self):
        from unittest.mock import patch
        engine, _ = self._engine()
        with patch.object(engine, "get_unresolved_promises", side_effect=RuntimeError("db down")):
            result = engine.get_payment_patterns("s1")
        assert isinstance(result, dict)
        assert result["reliability"] == "unknown"
