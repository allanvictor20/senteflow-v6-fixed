"""
Tests for MessageRouter and PendingStateManager.
No Evolution API or Firestore calls — all external I/O is mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.message_event import MessageEvent, MessageType
from integrations.whatsapp.message_router import MessageRouter, PendingStateManager


class FakeRepo:
    def __init__(self):
        self._convs = {}
        self.upsert_conversation = AsyncMock(return_value="ok")
        self.get_conversation = AsyncMock(return_value=None)
        self.append_conversation_timeline = MagicMock()

    async def get_conversation(self, org_id, sender_id):
        return self._convs.get(sender_id)

    async def upsert_conversation(self, org_id, sender_id, updates):
        self._convs[sender_id] = {**self._convs.get(sender_id, {}), **updates}
        return sender_id


def make_text_event(text="hello", sender="s1"):
    return MessageEvent(
        event_id="ev-1",
        message_id="msg-1",
        sender_id=sender,
        message_type=MessageType.TEXT,
        text=text,
    )


class TestPendingStateManager:
    @pytest.mark.asyncio
    async def test_set_and_get_pending(self):
        repo = FakeRepo()
        mgr = PendingStateManager(repo, "org-1")
        await mgr.set_pending_clarification("s1", "Sarah paid", "How much?")
        state = await mgr.get_pending_state("s1")
        assert state is not None
        assert state.get("pending_intent") == "awaiting_clarification"

    @pytest.mark.asyncio
    async def test_clear_pending(self):
        repo = FakeRepo()
        mgr = PendingStateManager(repo, "org-1")
        await mgr.set_pending_clarification("s1", "original", "question?")
        await mgr.clear_pending("s1")
        state = await mgr.get_pending_state("s1")
        assert state is None or state.get("pending_intent") is None

    @pytest.mark.asyncio
    async def test_get_pending_returns_none_when_empty(self):
        repo = FakeRepo()
        mgr = PendingStateManager(repo, "org-1")
        result = await mgr.get_pending_state("unknown-sender")
        assert result is None


class TestMessageRouter:
    def _make_router(self, repo=None):
        wa_client = MagicMock()
        wa_client.send_text = AsyncMock(return_value={"status": "ok"})
        return MessageRouter(
            wa_client=wa_client,
            repo=repo or FakeRepo(),
            org_id="org-1",
        )

    @pytest.mark.asyncio
    async def test_route_text_message_does_not_raise(self):
        router = self._make_router()
        event = make_text_event("Sarah paid 50k")
        with patch("integrations.whatsapp.message_router.extract_event", new=AsyncMock(
            return_value=MagicMock(
                event_type=MagicMock(value="payment_received"),
                confidence=0.9,
                entities={"amount": 50000},
                recommended_actions=[],
                reasoning="",
                sender_id="s1",
                raw_message="Sarah paid 50k",
                event_id="ev-x",
                transaction_id=None,
                to_summary=lambda: "payment",
            )
        )):
            with patch("integrations.whatsapp.message_router.ActionDispatcher") as MockDispatcher:
                MockDispatcher.return_value.dispatch = AsyncMock(
                    return_value=MagicMock(success=True, actions_executed=[], reply_text="ok")
                )
                await router.route(event)  # should not raise

    @pytest.mark.asyncio
    async def test_route_ignores_empty_text(self):
        router = self._make_router()
        event = make_text_event(text=None)
        # Should return early without calling AI
        with patch("integrations.whatsapp.message_router.extract_event") as mock_extract:
            await router.route(event)
            mock_extract.assert_not_called()