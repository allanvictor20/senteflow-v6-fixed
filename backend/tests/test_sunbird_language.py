"""
Sunbird AI client, the language pipeline (detect → translate → extractor sees
both) and reply translation. HTTP is served by httpx.MockTransport.
"""

import json

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from services.llm import sunbird
from services.llm.language_pipeline import normalize_business_text, translate_reply


@pytest.fixture
def sunbird_api(monkeypatch):
    """Route Sunbird calls to a handler; returns the list of requests seen."""
    monkeypatch.setenv("SUNBIRD_API_KEY", "test-token")
    monkeypatch.delenv("SUNBIRD_API_URL", raising=False)
    seen: list[httpx.Request] = []
    responses: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result = responses.get(request.url.path)
        if callable(result):
            result = result(request)
        if isinstance(result, httpx.Response):
            return result
        if result is None:
            return httpx.Response(404, json={"detail": "not mocked"})
        return httpx.Response(200, json=result)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sunbird.httpx, "AsyncClient", client_factory)
    return seen, responses


# ── client ───────────────────────────────────────────────────────────────────

async def test_translate_sends_auth_and_reads_output(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/translate"] = {
        "id": "x", "status": "COMPLETED",
        "output": {"translated_text": "Brian paid fifty thousand", "source_language": "lug", "target_language": "eng"},
    }
    text = await sunbird.translate("Brian asasudde emitwalo etaano", "eng", source_language="lug")
    assert text == "Brian paid fifty thousand"
    req = seen[0]
    assert str(req.url) == "https://api.sunbird.ai/tasks/translate"
    assert req.headers["authorization"] == "Bearer test-token"
    assert json.loads(req.content) == {
        "text": "Brian asasudde emitwalo etaano", "target_language": "eng", "source_language": "lug",
    }


async def test_language_id_not_detected(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/language_id"] = {"language": "language not detected", "confidence": None}
    assert await sunbird.identify_language("hmm okay then") == (None, None)


async def test_language_id_truncates_to_200_chars(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/language_id"] = {"language": "lug", "confidence": 0.97}
    assert await sunbird.identify_language("a" * 500) == ("lug", 0.97)
    assert len(json.loads(seen[0].content)["text"]) == 200


async def test_transcribe_sends_language_and_audio(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/audio/transcriptions"] = {"audio_transcription": " Brian asasudde ", "language": "lug"}
    assert await sunbird.transcribe(b"OggS...", "audio/ogg; codecs=opus", "lug") == "Brian asasudde"
    body = seen[0].content
    assert b'name="language"' in body and b"lug" in body and b'filename="voice.ogg"' in body


async def test_http_error_raises(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/translate"] = httpx.Response(503, json={"detail": "model loading"})
    with pytest.raises(sunbird.SunbirdError):
        await sunbird.translate("ssente", "eng", "lug")


# ── pipeline ─────────────────────────────────────────────────────────────────

async def test_luganda_message_is_translated(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/translate"] = {"output": {"translated_text": "I received money from Brian, 50k"}}
    result = await normalize_business_text("Nfunye ssente okuva ewa Brian 50k")
    assert result["language"] == "lug" and result["language_name"] == "Luganda"
    assert result["english_text"] == "I received money from Brian, 50k" and result["translated"]
    assert result["provider"] == "sunbird"
    # keyword detection was enough — no language_id call
    assert [r.url.path for r in seen] == ["/tasks/translate"]


async def test_english_message_makes_no_translation(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/language_id"] = {"language": "eng", "confidence": 0.99}
    result = await normalize_business_text("Brian paid 50k for cement")
    assert result["language"] == "eng" and not result["translated"]
    assert [r.url.path for r in seen] == ["/tasks/language_id"]


async def test_unsure_language_id_is_treated_as_english(sunbird_api):
    seen, responses = sunbird_api
    responses["/tasks/language_id"] = {"language": "lug", "confidence": 0.41}
    result = await normalize_business_text("Brian 50k ok")
    assert result["language"] == "eng"
    assert "/tasks/translate" not in [r.url.path for r in seen]


async def test_sunbird_detects_language_keywords_missed(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/language_id"] = {"language": "lug", "confidence": 0.93}
    responses["/tasks/translate"] = {"output": {"translated_text": "Brian has paid"}}
    result = await normalize_business_text("Brian asasudde")
    assert result["language"] == "lug" and result["detection_source"] == "sunbird"
    assert result["english_text"] == "Brian has paid"


async def test_translation_failure_passes_original_through(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/translate"] = httpx.Response(502, json={"detail": "empty output"})
    result = await normalize_business_text("Nfunye ssente 50k")
    assert result["english_text"] == "Nfunye ssente 50k" and not result["translated"]
    assert result["provider"] == "sunbird-failed" and result["language"] == "lug"


async def test_without_sunbird_nothing_is_called(monkeypatch):
    monkeypatch.delenv("SUNBIRD_API_KEY", raising=False)
    result = await normalize_business_text("Nfunye ssente 50k")
    assert result["provider"] == "not-configured" and result["language"] == "lug"
    assert result["english_text"] == "Nfunye ssente 50k"


def test_balance_is_not_luganda():
    from services.llm.language_pipeline import detect_language
    assert detect_language("what's my balance")["language"] == "english"


# ── reply translation ────────────────────────────────────────────────────────

async def test_reply_translation_keeps_numbers_and_footnote(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/translate"] = {"output": {"translated_text": "Brian asasudde UGX 50,000"}}
    out = await translate_reply("✅ Payment recorded\nBrian paid UGX 50,000\n_(confidence: 95%)_", "lug")
    assert out == "Brian asasudde UGX 50,000\n_(confidence: 95%)_"


async def test_reply_translation_that_loses_a_number_is_dropped(sunbird_api):
    _, responses = sunbird_api
    responses["/tasks/translate"] = {"output": {"translated_text": "Brian asasudde ssente"}}
    assert await translate_reply("Brian paid UGX 50,000", "lug") is None


async def test_english_replies_are_not_translated(sunbird_api):
    seen, _ = sunbird_api
    assert await translate_reply("Brian paid UGX 50,000", "eng") is None
    assert seen == []


# ── extractor prompt ─────────────────────────────────────────────────────────

def test_prompt_contains_original_and_translation():
    from services.llm.event_extractor import _build_message_section
    section = _build_message_section(
        "Nfunye ssente 50k",
        {"language": "lug", "language_name": "Luganda", "translated": True, "english_text": "I received money 50k"},
    )
    assert 'Message (original, Luganda): "Nfunye ssente 50k"' in section
    assert '"I received money 50k"' in section and "trust the original" in section
    assert _build_message_section("Brian paid 50k", None) == 'Message: "Brian paid 50k"'


# ── voice ────────────────────────────────────────────────────────────────────

async def test_voice_uses_sunbird_for_luganda_then_falls_back(monkeypatch):
    from services.llm import media_processor as mp
    monkeypatch.setenv("SUNBIRD_API_KEY", "t")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    whisper = AsyncMock(return_value="whisper text")
    monkeypatch.setattr(mp, "_transcribe_with_groq_whisper", whisper)

    with patch("services.llm.sunbird.transcribe", new=AsyncMock(return_value="Brian asasudde")) as sb:
        assert await mp.transcribe_audio_bytes(b"x", "audio/ogg", language="lug") == "Brian asasudde"
        sb.assert_awaited_once()
    whisper.assert_not_awaited()

    with patch("services.llm.sunbird.transcribe", new=AsyncMock(side_effect=sunbird.SunbirdError("down"))):
        assert await mp.transcribe_audio_bytes(b"x", "audio/ogg", language="lug") == "whisper text"

    with patch("services.llm.sunbird.transcribe", new=AsyncMock()) as sb:
        assert await mp.transcribe_audio_bytes(b"x", "audio/ogg", language="eng") == "whisper text"
        sb.assert_not_awaited()


# ── end to end through the router ────────────────────────────────────────────

OWNER = "256700000001"
OWNER_JID = f"{OWNER}@s.whatsapp.net"


@pytest.fixture
def luganda_system(monkeypatch, sunbird_api):
    from fastapi.testclient import TestClient
    from tests.fakes import FakeFirestore, FakeWhatsApp
    from bootstrap.dependency_injection import build_dependencies
    from bootstrap.app_factory import create_app

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OWNER_PHONES", f"+{OWNER}")
    for var in ("WEBHOOK_SECRET", "EVOLUTION_API_KEY", "EVOLUTION_API_TOKEN", "DEFAULT_OWNER_PHONE"):
        monkeypatch.delenv(var, raising=False)

    seen, responses = sunbird_api
    db = FakeFirestore()
    deps = build_dependencies(db=db, org_id="org-1")
    wa = FakeWhatsApp()
    deps.wa_client = wa
    deps._routers.clear()
    app = create_app(deps=deps)
    calls: list[dict] = []

    async def fake_llm(message, context, org_id="default", profile_repo=None, translation=None):
        calls.append({"message": message, "translation": translation})
        return {
            "event_type": "payment_received", "confidence": 0.95,
            "entities": {"customer": "Brian", "payer": "Brian", "amount": 50000},
            "reasoning": "t", "recommended_actions": [], "operational_effects": [],
        }

    n = {"i": 0}

    def send(text):
        n["i"] += 1
        msg = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": OWNER_JID, "fromMe": False, "id": f"L{n['i']}"},
                "pushName": "Owner", "messageType": "conversation",
                "message": {"conversation": text}, "messageTimestamp": 1767225600,
            },
        }
        with TestClient(app) as client:
            assert client.post("/api/webhooks/whatsapp", json=msg).status_code == 200
        return wa.sent[-1][1]

    from services.business_settings import invalidate
    invalidate("org-1")
    with patch("services.llm.event_extractor._call_llm", new=AsyncMock(side_effect=fake_llm)), \
         patch("tasks.reminder_sender.send_overdue_reminders", new=AsyncMock(return_value=0)):
        yield db, send, calls, responses, deps
    invalidate("org-1")


def test_luganda_message_reaches_ai_with_translation(luganda_system):
    db, send, calls, responses, _ = luganda_system
    responses["/tasks/translate"] = {"output": {"translated_text": "I received 50k from Brian"}}
    reply = send("Nfunye ssente 50k okuva ewa Brian")
    assert "Payment recorded" in reply  # replies stay English by default
    assert calls[0]["message"] == "Nfunye ssente 50k okuva ewa Brian"
    assert calls[0]["translation"]["english_text"] == "I received 50k from Brian"
    event = [e for e in db.collection_docs("organizations", "org-1", "events") if e.get("event_type") == "payment_received"][0]
    assert event["language"] == "lug" and event["translated_text"] == "I received 50k from Brian"


def test_replies_in_owner_language_when_asked(luganda_system):
    db, send, calls, responses, deps = luganda_system
    db.collection("organizations").document("org-1").collection("profile").document("config").set(
        {"org_id": "org-1", "reply_language": "same", "language": "lug"}
    )

    def translate(request):
        body = json.loads(request.content)
        if body["target_language"] == "eng":
            return {"output": {"translated_text": "I received 50k from Brian"}}
        return {"output": {"translated_text": "Ssente zisasuddwa: Brian UGX 50,000"}}

    responses["/tasks/translate"] = translate
    reply = send("Nfunye ssente 50k okuva ewa Brian")
    assert reply.startswith("Ssente zisasuddwa: Brian UGX 50,000")
