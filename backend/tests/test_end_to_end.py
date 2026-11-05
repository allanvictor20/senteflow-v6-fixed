"""
End-to-end: WhatsApp webhook → tenancy → router → extractor (faked LLM)
→ customer resolution → dispatcher → real TransactionRepository on an
in-memory Firestore → WhatsApp reply.

These exercise the owner-assistant flows that the unit tests (with stubbed
repos) could not catch.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from tests.fakes import FakeFirestore, FakeWhatsApp

OWNER = "256700000001"
OWNER_JID = f"{OWNER}@s.whatsapp.net"
STRANGER_JID = "256799999999@s.whatsapp.net"


def _webhook(jid: str, text: str, msg_id: str) -> dict:
    return {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": jid, "fromMe": False, "id": msg_id},
            "pushName": "Owner",
            "messageType": "conversation",
            "message": {"conversation": text},
            "messageTimestamp": 1767225600,
        },
    }


# What the (faked) LLM extracts for each owner message.
_LLM = {
    "Brian took cement on credit, 150k, will pay Friday": {
        "event_type": "debt_created", "confidence": 0.93,
        "entities": {"customer": "Brian", "debtor": "Brian", "amount": 150000, "item": "cement", "due_date": "2026-11-06"},
    },
    "Brian paid 50k": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Brian", "payer": "Brian", "amount": 50000},
    },
    "Brian paid the remaining 100k": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Brian", "payer": "Brian", "amount": 100000},
    },
    "who owes me money?": {
        "event_type": "business_query", "confidence": 0.9,
        "entities": {"query_type": "debts"},
    },
    "Sarah paid 800k for the fridge": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Sarah", "payer": "Sarah", "amount": 800000},
    },
    "John paid something": {
        "event_type": "payment_received", "confidence": 0.4,
        "entities": {"customer": "John", "payer": "John"},
    },
    "John paid something — 30k": {
        "event_type": "payment_received", "confidence": 0.9,
        "entities": {"customer": "John", "payer": "John", "amount": 30000},
    },
    "Peter ordered 3 bags of feed": {
        "event_type": "customer_order", "confidence": 0.9,
        "entities": {"customer": "Peter", "item": "feed", "quantity": 3},
    },
}


async def _fake_llm(message, context, org_id="default", profile_repo=None):
    data = _LLM.get(message, {"event_type": "unknown", "confidence": 0.1, "entities": {}})
    return {**data, "reasoning": "test", "recommended_actions": [], "operational_effects": []}


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OWNER_PHONES", f"+{OWNER}")
    for var in ("WEBHOOK_SECRET", "EVOLUTION_API_KEY", "EVOLUTION_API_TOKEN", "DEFAULT_OWNER_PHONE"):
        monkeypatch.delenv(var, raising=False)

    from bootstrap.dependency_injection import build_dependencies
    from bootstrap.app_factory import create_app

    db = FakeFirestore()
    deps = build_dependencies(db=db, org_id="org-1")
    wa = FakeWhatsApp()
    deps.wa_client = wa
    deps._routers.clear()
    app = create_app(deps=deps)

    counter = {"n": 0}

    def send(jid, text):
        counter["n"] += 1
        with TestClient(app) as client:
            resp = client.post("/api/webhooks/whatsapp", json=_webhook(jid, text, f"MSG{counter['n']}"))
        assert resp.status_code == 200, resp.text
        return wa.sent[-1][1] if wa.sent else None

    with patch("services.llm.event_extractor._call_llm", new=AsyncMock(side_effect=_fake_llm)), \
         patch("ai.reply_generator._groq_clarification", new=AsyncMock(return_value="How much did John pay?")), \
         patch("tasks.reminder_sender.send_overdue_reminders", new=AsyncMock(return_value=0)):
        yield db, wa, send


def _ledger(db):
    return [d for d in db.collection_docs("organizations", "org-1", "events") if d.get("transaction_type")]


def test_unregistered_number_gets_no_data(system):
    db, wa, send = system
    reply = send(STRANGER_JID, "who owes me money?")
    assert "isn't linked" in reply
    assert _ledger(db) == []


def test_debt_then_payment_with_approval_then_balance(system):
    db, wa, send = system

    reply = send(OWNER_JID, "Brian took cement on credit, 150k, will pay Friday")
    assert "Debt recorded" in reply and "Brian" in reply

    customers = [c for c in db.collection_docs("organizations", "org-1", "customers")]
    assert len(customers) == 1 and customers[0]["display_name"] == "Brian"
    brian_id = customers[0]["_id"]
    assert not brian_id.endswith("@s.whatsapp.net")  # keyed on the customer, not the owner

    reminders = db.collection_docs("organizations", "org-1", "reminders")
    assert len(reminders) == 1
    assert reminders[0]["customer_id"] == brian_id
    assert reminders[0]["due_at"].startswith("2026-11-07")  # morning after the promised date

    debt = [t for t in _ledger(db) if t["transaction_type"] == "debt_created"]
    assert debt and debt[0]["status"] == "approved" and debt[0]["customer_id"] == brian_id

    # Partial payment: recorded, reminder stays open, no question asked
    reply = send(OWNER_JID, "Brian paid 50k")
    assert "Payment recorded" in reply
    assert db.collection_docs("organizations", "org-1", "reminders")[0]["status"] == "pending"

    reply = send(OWNER_JID, "who owes me money?")
    assert "Brian" in reply and "100,000" in reply

    # Final payment settles the debt → asks before closing the reminder
    reply = send(OWNER_JID, "Brian paid the remaining 100k")
    assert "Reply YES" in reply
    assert len([t for t in _ledger(db) if t["transaction_type"] == "payment"]) == 2

    reply = send(OWNER_JID, "yes")
    assert "Payment recorded" in reply
    assert db.collection_docs("organizations", "org-1", "reminders")[0]["status"] == "resolved"
    assert len([t for t in _ledger(db) if t["transaction_type"] == "payment"]) == 2  # not recorded twice

    reply = send(OWNER_JID, "who owes me money?")
    assert "No outstanding debts" in reply


def test_large_payment_needs_approval_and_no_cancels(system):
    db, wa, send = system
    reply = send(OWNER_JID, "Sarah paid 800k for the fridge")
    assert "large transaction" in reply
    assert _ledger(db) == []  # nothing recorded yet — and the reply must not say it was

    reply = send(OWNER_JID, "no")
    assert "didn't record" in reply
    assert _ledger(db) == []


def test_large_payment_yes_records_once(system):
    db, wa, send = system
    send(OWNER_JID, "Sarah paid 800k for the fridge")
    reply = send(OWNER_JID, "yes")
    assert "Payment recorded" in reply
    payments = [t for t in _ledger(db) if t["transaction_type"] == "payment"]
    assert len(payments) == 1 and payments[0]["amount"] == 800000


def test_low_confidence_asks_before_recording(system):
    db, wa, send = system
    reply = send(OWNER_JID, "John paid something")
    assert reply == "How much did John pay?"
    assert _ledger(db) == []

    reply = send(OWNER_JID, "30k")
    assert "Payment recorded" in reply
    payments = [t for t in _ledger(db) if t["transaction_type"] == "payment"]
    assert len(payments) == 1 and payments[0]["amount"] == 30000


def test_ambiguous_customer_asks_which(system):
    db, wa, send = system
    from repositories.transaction_repository import TransactionRepository
    repo = TransactionRepository(db)
    repo.create_customer("org-1", "Brian Okello")
    repo.create_customer("org-1", "Brian Kato")

    reply = send(OWNER_JID, "Brian paid 50k")
    assert "Brian Okello" in reply and "Brian Kato" in reply
    assert _ledger(db) == []


def test_order_creates_order_document(system):
    db, wa, send = system
    reply = send(OWNER_JID, "Peter ordered 3 bags of feed")
    assert "Order noted" in reply and "Peter" in reply

    from domain.orders.model import Order
    orders = db.collection_docs("organizations", "org-1", "orders")
    assert len(orders) == 1
    order = Order(**{k: v for k, v in orders[0].items() if k != "_id"})  # parses as an Order
    assert order.items[0].name == "feed" and order.items[0].quantity == 3


def test_duplicate_webhook_processed_once(system):
    db, wa, send = system
    from fastapi.testclient import TestClient  # noqa: F401
    # Same WhatsApp message id twice → second is a duplicate.
    from api.routes import whatsapp as wa_routes
    payload = _webhook(OWNER_JID, "Brian took cement on credit, 150k, will pay Friday", "SAME-ID")
    from bootstrap.app_factory import create_app
    app = create_app(deps=wa_routes._deps)
    with TestClient(app) as client:
        first = client.post("/api/webhooks/whatsapp", json=payload).json()
        second = client.post("/api/webhooks/whatsapp", json=payload).json()
    assert first["status"] == "queued" and second["status"] == "duplicate"
    assert len([t for t in _ledger(db) if t["transaction_type"] == "debt_created"]) == 1
