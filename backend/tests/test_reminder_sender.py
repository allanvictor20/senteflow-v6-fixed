"""
Tests for reminder_sender — covers the datetime comparison fix (Bug 5).

The key fix: replaced ISO-string lexicographic comparison with proper
datetime object comparison, which correctly handles timestamps that
have "Z" suffixes or "+00:00" timezones.
"""
import pytest
from datetime import timedelta

from unittest.mock import MagicMock, AsyncMock, patch
from tasks.reminder_sender import send_overdue_reminders, _format_reminder_message
from utils.clock import utc_now


def _make_doc(doc_id, created_at, sender_id="owner-123", debtor="Brian", amount=50000):
    """Build a fake Firestore doc mock for a reminder."""
    data = {
        "status": "pending",
        "created_at": created_at,
        "sender_id": sender_id,
        "debtor": debtor,
        "amount": amount,
        "currency": "UGX",
        "due_date_display": "Mon 02 Jun",
    }
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = data
    doc.reference.update = MagicMock()
    return doc


def _make_repo(docs):
    """Build a fake repo whose reminder collection returns the given docs."""
    repo = MagicMock()
    # Chain: repo._db.collection(...).document(...).collection(...).where(...).get()
    col_mock = MagicMock()
    col_mock.where.return_value = col_mock
    col_mock.get.return_value = docs
    repo._db.collection.return_value.document.return_value.collection.return_value = col_mock
    return repo


class TestDatetimeComparison:
    """The pre-fix code used string comparison; these tests verify datetime comparison."""

    @pytest.mark.asyncio
    async def test_overdue_reminder_is_sent(self):
        """A reminder created 48h ago should be sent."""
        old_ts = (utc_now() - timedelta(hours=48)).isoformat()
        doc = _make_doc("doc-1", old_ts)
        repo = _make_repo([doc])

        wa_client = AsyncMock()
        sent = await send_overdue_reminders(wa_client, repo, "org-1", overdue_hours=24)
        assert sent == 1
        wa_client.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_recent_reminder_is_not_sent(self):
        """A reminder created 1h ago should NOT be sent (not yet overdue)."""
        new_ts = (utc_now() - timedelta(hours=1)).isoformat()
        doc = _make_doc("doc-2", new_ts)
        repo = _make_repo([doc])

        wa_client = AsyncMock()
        sent = await send_overdue_reminders(wa_client, repo, "org-1", overdue_hours=24)
        assert sent == 0
        wa_client.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_z_suffix_timestamp_handled_correctly(self):
        """Timestamps with trailing 'Z' must parse correctly (pre-fix they could fail)."""
        old_ts = (utc_now() - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = _make_doc("doc-3", old_ts)
        repo = _make_repo([doc])

        wa_client = AsyncMock()
        sent = await send_overdue_reminders(wa_client, repo, "org-1", overdue_hours=24)
        assert sent == 1

    @pytest.mark.asyncio
    async def test_bad_timestamp_is_skipped_not_raised(self):
        """An unparseable timestamp should be skipped, not crash the sender."""
        doc = _make_doc("doc-4", "not-a-timestamp")
        repo = _make_repo([doc])

        wa_client = AsyncMock()
        # Must not raise
        sent = await send_overdue_reminders(wa_client, repo, "org-1", overdue_hours=24)
        assert sent == 0

    @pytest.mark.asyncio
    async def test_reminder_marked_notified_after_send(self):
        """After sending, the reminder doc must be updated to status='notified'."""
        old_ts = (utc_now() - timedelta(hours=48)).isoformat()
        doc = _make_doc("doc-5", old_ts)
        repo = _make_repo([doc])

        wa_client = AsyncMock()
        await send_overdue_reminders(wa_client, repo, "org-1", overdue_hours=24)
        doc.reference.update.assert_called_once()
        update_call = doc.reference.update.call_args[0][0]
        assert update_call["status"] == "notified"


class TestFormatReminderMessage:
    def test_includes_debtor_and_amount(self):
        msg = _format_reminder_message({"debtor": "Sarah", "amount": 75000, "currency": "UGX", "due_date_display": "Fri"})
        assert "Sarah" in msg
        assert "75,000" in msg

    def test_handles_missing_amount(self):
        msg = _format_reminder_message({"debtor": "John", "due_date_display": "soon"})
        assert "John" in msg
        # Should not crash with missing amount
