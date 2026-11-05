"""Unit tests for the v7 fixes that the end-to-end tests don't cover directly."""
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Tenancy ───────────────────────────────────────────────────────────────────

def test_normalize_phone_variants(monkeypatch):
    from services.tenancy import normalize_phone
    monkeypatch.setenv("DEFAULT_COUNTRY_CODE", "256")
    assert normalize_phone("+256 700-000001") == "256700000001"
    assert normalize_phone("256700000001@s.whatsapp.net") == "256700000001"
    assert normalize_phone("0700000001") == "256700000001"
    assert normalize_phone("00256700000001") == "256700000001"
    assert normalize_phone("") == ""


def test_unknown_number_has_no_org(monkeypatch):
    from services.tenancy import TenantDirectory
    from tests.fakes import FakeFirestore
    monkeypatch.setenv("OWNER_PHONES", "+256700000001")
    tenants = TenantDirectory(FakeFirestore(), "org-1")
    assert tenants.org_for_phone("256700000001@s.whatsapp.net") == "org-1"
    assert tenants.org_for_phone("256799999999@s.whatsapp.net") is None


def test_link_phone_routes_to_other_org(monkeypatch):
    from services.tenancy import TenantDirectory, PhoneAlreadyLinked
    from tests.fakes import FakeFirestore
    monkeypatch.delenv("OWNER_PHONES", raising=False)
    monkeypatch.delenv("DEFAULT_OWNER_PHONE", raising=False)
    tenants = TenantDirectory(FakeFirestore(), "org-1")
    tenants.link_phone("org-2", "+256 711 000 222")
    assert tenants.org_for_phone("256711000222@s.whatsapp.net") == "org-2"
    assert "org-2" in tenants.list_org_ids()
    with pytest.raises(PhoneAlreadyLinked):
        tenants.link_phone("org-3", "0711000222")


# ── Dates ─────────────────────────────────────────────────────────────────────

def test_parse_due_date_words_and_formats():
    from utils.dates import parse_due_date
    thursday = date(2026, 11, 5)
    assert parse_due_date("Friday", today=thursday) == date(2026, 11, 6)
    assert parse_due_date("by friday", today=thursday) == date(2026, 11, 6)
    assert parse_due_date("tomorrow", today=thursday) == date(2026, 11, 6)
    assert parse_due_date("2026-12-01", today=thursday) == date(2026, 12, 1)
    assert parse_due_date("01/12/2026", today=thursday) == date(2026, 12, 1)
    assert parse_due_date("end of month", today=thursday) == date(2026, 11, 30)
    assert parse_due_date("someday", today=thursday) is None


def test_reminder_due_at_morning_after_in_kampala(monkeypatch):
    from utils.dates import reminder_due_at
    monkeypatch.setenv("BUSINESS_TZ", "Africa/Kampala")
    due = reminder_due_at("2026-11-06", after_due_date=True)
    assert due.isoformat() == "2026-11-07T06:00:00"  # 09:00 EAT == 06:00 UTC


# ── Voice notes: no event loop in worker threads ─────────────────────────────

def test_run_coro_blocking_works_inside_to_thread():
    from ai.extractor import _run_coro_blocking

    async def coro():
        return "transcript"

    async def main():
        return await asyncio.to_thread(lambda: _run_coro_blocking(coro()))

    assert asyncio.run(main()) == "transcript"


# ── Media download uses Evolution's decrypting endpoint ──────────────────────

@pytest.mark.asyncio
async def test_media_download_prefers_decrypted_base64(tmp_path, monkeypatch):
    import services.llm.media_processor as mp
    monkeypatch.setattr(mp, "_STORAGE_BASE", str(tmp_path))
    wa = MagicMock()
    wa.get_media_bytes = AsyncMock(return_value=b"JPEGDATA")
    wa.download_media = AsyncMock(return_value=b"ENCRYPTED")
    path = await mp.download_and_save_media(
        media_url="https://mmg.whatsapp.net/v/t62/abc.enc?x=1",
        mime_type="image/jpeg",
        sender_id="256700000001@s.whatsapp.net",
        source_hint="receipt",
        wa_client=wa,
        message_id="MSG1",
    )
    assert open(path, "rb").read() == b"JPEGDATA"
    wa.download_media.assert_not_called()


@pytest.mark.asyncio
async def test_media_download_never_saves_encrypted_cdn_file(tmp_path, monkeypatch):
    import services.llm.media_processor as mp
    monkeypatch.setattr(mp, "_STORAGE_BASE", str(tmp_path))
    monkeypatch.setattr(mp, "_RETRY_DELAY_SECONDS", 0)
    wa = MagicMock()
    wa.get_media_bytes = AsyncMock(return_value=None)
    wa.download_media = AsyncMock(return_value=b"ENCRYPTED")
    with pytest.raises(mp.MediaDownloadError):
        await mp.download_and_save_media(
            media_url="https://mmg.whatsapp.net/v/t62/abc.enc",
            mime_type="image/jpeg", sender_id="s", source_hint="receipt",
            wa_client=wa, message_id="MSG1",
        )
    wa.download_media.assert_not_called()


# ── Models ────────────────────────────────────────────────────────────────────

def test_retired_groq_models_fall_back_to_current(monkeypatch):
    from services.llm import models
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
    assert models.text_model() == models.DEFAULT_TEXT_MODEL
    assert models.vision_model() == models.DEFAULT_VISION_MODEL
    monkeypatch.setenv("GROQ_MODEL", "some/new-model")
    assert models.text_model() == "some/new-model"


def test_clean_completion_strips_reasoning_and_fences():
    from services.llm.models import clean_completion
    assert clean_completion('<think>hmm</think>```json\n{"a": 1}\n```') == '{"a": 1}'


# ── Debts from the ledger ─────────────────────────────────────────────────────

def test_debt_reply_counts_debt_created_minus_payments():
    from services.responses.reply_generator import generate_debt_reply
    txns = [
        {"transaction_type": "debt_created", "amount": 150000, "customer_id": "c1", "customer_name": "Brian", "status": "approved"},
        {"transaction_type": "payment", "amount": 50000, "customer_id": "c1", "payer": "Brian", "status": "approved"},
        {"transaction_type": "debt_created", "amount": 20000, "debtor": "Sarah", "status": "approved"},
        {"transaction_type": "payment", "amount": 999, "payer": "Ghost", "status": "rejected"},
    ]
    reply = generate_debt_reply(txns)
    assert "Brian: UGX 100,000" in reply and "Sarah: UGX 20,000" in reply
    assert "Ghost" not in reply
    assert "fully paid" in generate_debt_reply(
        txns + [{"transaction_type": "payment", "amount": 100000, "customer_id": "c1", "status": "approved"}], "brian"
    )


# ── Webhook: group chats are ignored ──────────────────────────────────────────

def test_group_messages_ignored():
    from integrations.whatsapp.webhook_handler import normalize_evolution_event
    payload = {
        "event": "messages.upsert",
        "data": {"key": {"remoteJid": "1203630@g.us", "fromMe": False, "id": "X"},
                 "messageType": "conversation", "message": {"conversation": "hi"}},
    }
    assert normalize_evolution_event(payload) is None


# ── Reminders only marked notified when the send worked ──────────────────────

@pytest.mark.asyncio
async def test_failed_reminder_send_stays_pending():
    from tasks.reminder_sender import send_overdue_reminders
    doc = MagicMock()
    doc.id = "r1"
    doc.to_dict.return_value = {
        "status": "pending", "created_at": "2020-01-01T00:00:00", "sender_id": "owner@s.whatsapp.net",
        "debtor": "Brian", "amount": 5000,
    }
    repo = MagicMock()
    col = MagicMock()
    col.where.return_value = col
    col.get.return_value = [doc]
    repo._db.collection.return_value.document.return_value.collection.return_value = col
    repo.list_transactions.return_value = []
    wa = AsyncMock()
    wa.send_text.return_value = {"error": "instance not connected"}
    sent = await send_overdue_reminders(wa, repo, "org-1")
    assert sent == 0
    doc.reference.update.assert_not_called()


# ── Text-to-speech never reads out formatting ─────────────────────────────────

def test_speakable_strips_markup_and_emoji():
    from integrations.whatsapp.reply_sender import _speakable
    assert _speakable("✅ *Payment recorded*\nBrian paid _UGX 50,000_") == "Payment recorded\nBrian paid UGX 50,000"
