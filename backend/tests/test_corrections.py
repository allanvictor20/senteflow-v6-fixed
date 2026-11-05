"""
Corrections over WhatsApp, end to end: webhook → router → corrections →
real repository on the in-memory Firestore. The LLM is faked.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from tests.fakes import FakeFirestore, FakeWhatsApp

OWNER = "256700000001"
OWNER_JID = f"{OWNER}@s.whatsapp.net"
ORG = "org-1"


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


def _correction(**entities):
    return {"event_type": "correction", "confidence": 0.9, "entities": entities}


_LLM = {
    "Brian took cement on credit, 150k, will pay Friday": {
        "event_type": "debt_created", "confidence": 0.93,
        "entities": {"customer": "Brian", "debtor": "Brian", "amount": 150000, "item": "cement", "due_date": "2026-11-06"},
    },
    "Brian paid 50k": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Brian", "payer": "Brian", "amount": 50000},
    },
    "Brian paid 150k": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Brian", "payer": "Brian", "amount": 150000},
    },
    "Sarah paid 30k": {
        "event_type": "payment_received", "confidence": 0.95,
        "entities": {"customer": "Sarah", "payer": "Sarah", "amount": 30000},
    },
    "Brian will pay 70k on Friday": {
        "event_type": "payment_promise", "confidence": 0.92,
        "entities": {"customer": "Brian", "debtor": "Brian", "amount": 70000, "due_date": "2026-11-06"},
    },
    "who owes me money?": {"event_type": "business_query", "confidence": 0.9, "entities": {"query_type": "debts"}},
    "Brian paid 40k not 50k": _correction(
        correction_action="change", target_customer="Brian", target_type="payment",
        target_amount=50000, new_amount=40000,
    ),
    "it was Kato not Brian": _correction(correction_action="change", target_customer="Brian", new_customer="Kato"),
    "Brian will pay Monday not Friday": _correction(
        correction_action="change", target_customer="Brian", target_type="promise", new_due_date="2026-11-09",
    ),
    "that was an expense not a payment": _correction(correction_action="change", target_type="payment", new_type="expense"),
    "delete the fuel expense": _correction(correction_action="undo", target_type="expense"),
    "remove Peter's payment": _correction(correction_action="undo", target_customer="Peter", target_type="payment"),
    "that's wrong": _correction(correction_action="change"),
}


async def _fake_llm(message, context, org_id="default", profile_repo=None, **kwargs):
    data = _LLM.get(message, {"event_type": "unknown", "confidence": 0.1, "entities": {}})
    return {**data, "reasoning": "test", "recommended_actions": [], "operational_effects": []}


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OWNER_PHONES", f"+{OWNER}")
    for var in ("WEBHOOK_SECRET", "EVOLUTION_API_KEY", "EVOLUTION_API_TOKEN", "DEFAULT_OWNER_PHONE", "SUNBIRD_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from bootstrap.dependency_injection import build_dependencies
    from bootstrap.app_factory import create_app

    db = FakeFirestore()
    deps = build_dependencies(db=db, org_id=ORG)
    wa = FakeWhatsApp()
    deps.wa_client = wa
    deps._routers.clear()
    app = create_app(deps=deps)
    counter = {"n": 0}

    def send(text):
        counter["n"] += 1
        with TestClient(app) as client:
            resp = client.post("/api/webhooks/whatsapp", json=_webhook(OWNER_JID, text, f"MSG{counter['n']}"))
        assert resp.status_code == 200, resp.text
        return wa.sent[-1][1] if wa.sent else None

    with patch("services.llm.event_extractor._call_llm", new=AsyncMock(side_effect=_fake_llm)), \
         patch("tasks.reminder_sender.send_overdue_reminders", new=AsyncMock(return_value=0)):
        yield db, send


def _ledger(db, active_only=True):
    rows = [d for d in db.collection_docs("organizations", ORG, "events") if d.get("transaction_type")]
    return [r for r in rows if r.get("status") != "rejected"] if active_only else rows


def _payments(db, active_only=True):
    return [t for t in _ledger(db, active_only) if t["transaction_type"] == "payment"]


def test_undo_last_payment(system):
    db, send = system
    send("Brian took cement on credit, 150k, will pay Friday")
    send("Brian paid 50k")

    reply = send("undo")
    assert "Remove this record?" in reply and "Brian paid UGX 50,000" in reply
    assert len(_payments(db)) == 1  # nothing changes before YES

    reply = send("yes")
    assert "Removed" in reply
    assert _payments(db) == []
    removed = _payments(db, active_only=False)[0]
    assert removed["status"] == "rejected" and removed["rejected_by"] == f"whatsapp:{OWNER}"
    events = [e for e in db.collection_docs("organizations", ORG, "events") if e.get("event_type") == "payment_received"]
    assert events[0]["processing_status"] == "cancelled" and events[0]["undone_by"] == f"whatsapp:{OWNER}"

    assert "150,000" in send("who owes me money?")


def test_no_keeps_the_record(system):
    db, send = system
    send("Brian paid 50k")
    send("delete that")
    assert "left it as it was" in send("no")
    assert len(_payments(db)) == 1


def test_change_amount(system):
    db, send = system
    send("Brian took cement on credit, 150k, will pay Friday")
    send("Brian paid 50k")
    old = _payments(db)[0]

    reply = send("Brian paid 40k not 50k")
    assert "Before:" in reply and "UGX 50,000" in reply and "After:" in reply and "UGX 40,000" in reply

    reply = send("yes")
    assert reply.startswith("✏️ *Fixed*") and "40,000" in reply and "confidence" not in reply
    payments = _payments(db)
    assert len(payments) == 1 and payments[0]["amount"] == 40000
    assert payments[0]["corrects"] == old["_id"]
    assert payments[0]["created_at"] == old["created_at"]  # keeps its place in the history
    assert payments[0]["customer_id"] == old["customer_id"]

    assert "110,000" in send("who owes me money?")


def test_change_customer(system):
    db, send = system
    send("Brian took cement on credit, 150k, will pay Friday")
    send("Brian paid 50k")

    reply = send("it was Kato not Brian")
    assert "Kato paid UGX 50,000" in reply
    send("yes")

    payments = _payments(db)
    assert len(payments) == 1 and payments[0]["customer_name"] == "Kato" and payments[0]["payer"] == "Kato"
    kato = [c for c in db.collection_docs("organizations", ORG, "customers") if c["display_name"] == "Kato"]
    assert kato and payments[0]["customer_id"] == kato[0]["_id"]
    owed = send("who owes me money?")
    assert "Brian" in owed and "150,000" in owed


def test_undoing_a_settling_payment_reopens_the_reminder(system):
    db, send = system
    send("Brian took cement on credit, 150k, will pay Friday")
    assert "Reply YES" in send("Brian paid 150k")
    send("yes")
    assert db.collection_docs("organizations", ORG, "reminders")[0]["status"] == "resolved"

    send("undo")
    reply = send("yes")
    assert "open again" in reply
    reminder = db.collection_docs("organizations", ORG, "reminders")[0]
    assert reminder["status"] == "pending" and reminder.get("resolved_by_event") is None


def test_change_promise_date_moves_the_reminder(system):
    db, send = system
    send("Brian will pay 70k on Friday")
    reply = send("Brian will pay Monday not Friday")
    assert "Promise" in reply and "Mon 09 Nov" in reply
    send("yes")

    reminders = db.collection_docs("organizations", ORG, "reminders")
    by_status = {r["status"]: r for r in reminders}
    assert set(by_status) == {"cancelled", "pending"}
    assert by_status["pending"]["due_at"].startswith("2026-11-10")
    promises = db.collection_docs("organizations", ORG, "promises")
    assert {p["status"] for p in promises} == {"cancelled", "pending"}


def test_change_type_payment_to_expense(system):
    db, send = system
    send("Sarah paid 30k")
    send("that was an expense not a payment")
    send("yes")
    active = _ledger(db)
    assert len(active) == 1 and active[0]["transaction_type"] == "expense" and active[0]["amount"] == 30000


def test_receipt_entry_can_be_undone(system):
    db, send = system
    from repositories.transaction_repository import TransactionRepository
    repo = TransactionRepository(db)
    ids, _ = repo.save_approved_batch(
        [{"amount": 40000, "transaction_type": "expense", "description": "fuel", "currency": "UGX",
          "recorded_by": OWNER_JID, "sender_id": OWNER_JID}],
        ORG, "whatsapp:256700000001", "session-1",
    )
    reply = send("delete the fuel expense")
    assert "Expense — UGX 40,000 for fuel" in reply
    send("yes")
    doc = repo.get_transaction(ORG, ids[0])
    assert doc["status"] == "rejected" and "Undone over WhatsApp" in doc["rejection_reason"]


def test_no_match_offers_a_numbered_choice(system):
    db, send = system
    send("Brian paid 50k")
    send("Sarah paid 30k")
    reply = send("remove Peter's payment")
    assert "Which one do you mean?" in reply and "1." in reply and "2." in reply
    assert "Sarah paid" in reply.split("1.")[1].split("2.")[0]  # newest first

    reply = send("2")
    assert "Remove this record?" in reply and "Brian paid UGX 50,000" in reply
    send("yes")
    assert [p["payer"] for p in _payments(db)] == ["Sarah"]


def test_vague_correction_asks_what_to_change(system):
    db, send = system
    send("Brian paid 50k")
    reply = send("that's wrong")
    assert "What should it say instead?" in reply and "Brian paid UGX 50,000" in reply
    assert len(_payments(db)) == 1


def test_customer_totals_recomputed_after_undo(system):
    db, send = system
    send("Brian took cement on credit, 150k, will pay Friday")
    send("Brian paid 50k")
    brian = [c for c in db.collection_docs("organizations", ORG, "customers")][0]["_id"]
    memory = {d["_id"]: d for d in db.collection_docs("organizations", ORG, "memory")}
    assert memory[brian]["total_paid"] == 50000
    send("undo")
    send("yes")
    memory = {d["_id"]: d for d in db.collection_docs("organizations", ORG, "memory")}
    assert memory[brian]["total_paid"] == 0 and memory[brian]["total_outstanding"] == 150000


def test_yes_to_a_numbered_list_asks_for_the_number(system):
    db, send = system
    send("Brian paid 50k")
    send("remove Peter's payment")
    assert "Reply with a number from 1 to 1" in send("yes")
    assert "Remove this record?" in send("1")
    send("yes")
    assert _payments(db) == []


def test_undo_command_patterns():
    from services.corrections import is_undo_command
    for text in ("undo", "Undo!", "delete that", "remove the last one", "delete last entry", "sazaamu", "undo it"):
        assert is_undo_command(text), text
    for text in ("delete Brian's payment", "undo the payment from Monday", "remove 2 bags from stock", "Brian paid 50k"):
        assert not is_undo_command(text), text


def test_find_matches_rules():
    from services.corrections import Record, find_matches
    recs = [
        Record(id="3", kind="event", family="payment", amount=50000, customer="Brian Okello", when="3", sender="me"),
        Record(id="2", kind="event", family="debt", amount=150000, customer="Brian Okello", when="2", sender="me"),
        Record(id="1", kind="ledger", family="payment", amount=20000, customer="Sarah", when="1", sender="staff"),
    ]
    assert [r.id for r in find_matches(recs, {}, "staff")] == ["1"]          # my last record
    assert [r.id for r in find_matches(recs, {}, "nobody")] == ["3"]         # else the last one
    assert [r.id for r in find_matches(recs, {"target_customer": "okello"}, "me")] == ["3", "2"]
    assert [r.id for r in find_matches(recs, {"target_customer": "Brian", "target_type": "debt"}, "me")] == ["2"]
    assert [r.id for r in find_matches(recs, {"target_amount": 20000}, "me")] == ["1"]
    assert find_matches(recs, {"target_amount": 99000}, "me") == []          # amount was the only clue
    # wrong old amount but customer + type match → still found
    assert [r.id for r in find_matches(recs, {"target_customer": "Brian", "target_type": "payment", "target_amount": 60000}, "me")] == ["3"]
