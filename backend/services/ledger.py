"""
SenteFlow — saving transactions extracted from files
====================================================
Shared by the WhatsApp receipt path and the dashboard /approve endpoint so
both store extracted transactions the same way.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TXN_TO_EVENT_TYPE = {
    "payment": "payment_received",
    "income": "payment_received",
    "contribution": "payment_received",
    "expense": "expense_recorded",
    "debt_created": "debt_created",
    "payment_promise": "payment_promise",
}

_CUSTOMER_TYPES = {"payment", "income", "contribution", "debt_created", "payment_promise"}


def prepare_extracted_records(repo, org_id: str, records: list[dict], recorded_by: str = "") -> list[dict]:
    """
    - event_type follows the transaction type (a MoMo "payment received"
      screenshot is income — it used to be stored as an expense).
    - payments/debts are linked to the customer named as payer/member.
    """
    from services.conversation.customer_resolution import CustomerResolver

    resolver = CustomerResolver(repo, org_id)
    prepared: list[dict] = []
    for item in records:
        record = dict(item)
        txn_type = record.get("transaction_type") or "payment"
        record["event_type"] = TXN_TO_EVENT_TYPE.get(txn_type, "expense_recorded")
        if recorded_by:
            record.setdefault("recorded_by", recorded_by)
            record.setdefault("sender_id", recorded_by)
        if txn_type in _CUSTOMER_TYPES and not record.get("customer_id"):
            name = record.get("payer") or record.get("member_name")
            if name:
                try:
                    res = resolver.resolve_name(str(name))
                    if res.customer_id:
                        record["customer_id"] = res.customer_id
                        record["customer_name"] = res.display_name
                except Exception as exc:
                    logger.warning("extracted_customer_resolve_failed", extra={"error": str(exc)})
        prepared.append(record)
    return prepared
