"""
SenteFlow — HandlePaymentWorkflow
====================================
Processes payment events: records the payment and reduces outstanding debts.
"""

import logging
from domain.events import BusinessEvent, EventType

logger = logging.getLogger(__name__)

PAYMENT_EVENT_TYPES = {
    EventType.PAYMENT_RECEIVED,
    EventType.PAYMENT_PROMISE,
    EventType.DEBT_CREATED,
    EventType.EXPENSE_RECORDED,
}


async def handle_payment(event: BusinessEvent, org_id: str, repo) -> dict:
    """
    Process a financial event.
    Returns a summary dict: {event_id, ledger_id, amount, type}.
    """
    if event.event_type not in PAYMENT_EVENT_TYPES:
        return {}

    entities = event.entities or {}
    amount = entities.get("amount")
    if not amount:
        return {}

    type_map = {
        EventType.PAYMENT_RECEIVED: "payment_received",
        EventType.EXPENSE_RECORDED: "expense",
        EventType.DEBT_CREATED: "debt_created",
        EventType.PAYMENT_PROMISE: "payment_promise",
    }
    ledger_type = type_map.get(event.event_type, "payment")

    try:
        ledger_id = await _maybe_await(repo.save_transaction(org_id, {
            "amount": float(amount),
            "currency": entities.get("currency", "UGX"),
            "transaction_type": ledger_type,
            "category": entities.get("category", "other"),
            "description": event.reasoning or event.raw_message[:100],
            "payer": entities.get("payer") or entities.get("sender"),
            "payee": entities.get("payee"),
            "notes": f"[event:{event.event_id}]",
        }, event.sender_id))
        event.transaction_id = ledger_id
        logger.info("payment_recorded", extra={"ledger_id": ledger_id, "amount": amount, "type": ledger_type})
        return {"event_id": event.event_id, "ledger_id": ledger_id, "amount": amount, "type": ledger_type}
    except Exception as exc:
        logger.error("payment_record_failed", extra={"error": str(exc)})
        return {}


import inspect


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value
