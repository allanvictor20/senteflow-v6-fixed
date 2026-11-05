"""
Business profile settings: stored on the server, editable from the dashboard,
and used by the WhatsApp assistant (currency for unlabeled amounts + replies).
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from tests.fakes import FakeFirestore, FakeWhatsApp

OWNER = "256700000001"
OWNER_JID = f"{OWNER}@s.whatsapp.net"


def _webhook(text, msg_id):
    return {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": OWNER_JID, "fromMe": False, "id": msg_id},
            "messageType": "conversation",
            "message": {"conversation": text},
        },
    }


_LLM = {
    "Wanjiru took flour on credit, 3000": {
        "event_type": "debt_created", "confidence": 0.95,
        "entities": {"customer": "Wanjiru", "debtor": "Wanjiru", "amount": 3000},
    },
    "who owes me?": {"event_type": "business_query", "confidence": 0.9, "entities": {"query_type": "debts"}},
}


async def _fake_llm(message, context, org_id="default", profile_repo=None):
    data = _LLM.get(message, {"event_type": "unknown", "confidence": 0.1, "entities": {}})
    return {**data, "reasoning": "", "recommended_actions": [], "operational_effects": []}


@pytest.fixture
def app_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OWNER_PHONES", f"+{OWNER}")
    for var in ("WEBHOOK_SECRET", "EVOLUTION_API_KEY", "EVOLUTION_API_TOKEN", "DEFAULT_OWNER_PHONE"):
        monkeypatch.delenv(var, raising=False)

    from bootstrap.dependency_injection import build_dependencies
    from bootstrap.app_factory import create_app
    from core.auth import verify_firebase_token
    from services import business_settings

    business_settings._cache.clear()
    db = FakeFirestore()
    deps = build_dependencies(db=db, org_id="org-1")
    wa = FakeWhatsApp()
    deps.wa_client = wa
    deps._routers.clear()
    app = create_app(deps=deps)
    app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "u1", "email": "owner@example.com"}

    with patch("core.auth.ensure_org_access", side_effect=lambda token, org_id: token), \
         patch("services.llm.event_extractor._call_llm", new=AsyncMock(side_effect=_fake_llm)), \
         patch("tasks.reminder_sender.send_overdue_reminders", new=AsyncMock(return_value=0)):
        with TestClient(app) as client:
            yield client, db, wa
    business_settings._cache.clear()


def test_profile_defaults_then_saves(app_env):
    client, db, wa = app_env
    r = client.get("/api/org/profile", params={"org_id": "org-1"})
    assert r.status_code == 200
    assert r.json()["currency"] == "UGX" and r.json()["name"] == ""

    r = client.put("/api/org/profile", params={"org_id": "org-1"},
                   json={"name": "  Kamu General Supplies ", "currency": "kes", "credit_policy": "Max 200k"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Kamu General Supplies" and body["currency"] == "KES"

    stored = db.docs[("organizations", "org-1", "profile", "config")]
    assert stored["name"] == "Kamu General Supplies" and stored["credit_policy"] == "Max 200k"

    # Partial update keeps the other fields
    client.put("/api/org/profile", params={"org_id": "org-1"}, json={"location": "Kawempe"})
    stored = db.docs[("organizations", "org-1", "profile", "config")]
    assert stored["location"] == "Kawempe" and stored["currency"] == "KES"


def test_profile_rejects_unknown_currency(app_env):
    client, db, wa = app_env
    r = client.put("/api/org/profile", params={"org_id": "org-1"}, json={"currency": "EUR"})
    assert r.status_code == 400


def test_profile_is_injected_into_the_ai_prompt(app_env):
    client, db, wa = app_env
    client.put("/api/org/profile", params={"org_id": "org-1"},
               json={"name": "Kamu General Supplies", "notes": "Bags means 50kg cement bags"})
    import asyncio
    from repositories.business_profile_repository import BusinessProfileRepository
    from services.llm.event_extractor import _build_system_prompt
    prompt = asyncio.run(_build_system_prompt("org-1", BusinessProfileRepository(db)))
    assert "Kamu General Supplies" in prompt and "50kg cement bags" in prompt


def test_business_currency_used_in_whatsapp_replies(app_env):
    client, db, wa = app_env
    client.put("/api/org/profile", params={"org_id": "org-1"}, json={"currency": "KES"})

    client.post("/api/webhooks/whatsapp", json=_webhook("Wanjiru took flour on credit, 3000", "M1"))
    assert "KES 3,000" in wa.sent[-1][1]
    debt = [d for d in db.collection_docs("organizations", "org-1", "events") if d.get("transaction_type") == "debt_created"]
    assert debt[0]["currency"] == "KES"

    client.post("/api/webhooks/whatsapp", json=_webhook("who owes me?", "M2"))
    assert "Wanjiru: KES 3,000" in wa.sent[-1][1]
