"""
SenteFlow — HandleOrderWorkflow
==================================
Creates and updates Order records when order-related BusinessEvents arrive.
"""

import logging
from domain.events import BusinessEvent, EventType

logger = logging.getLogger(__name__)

ORDER_EVENT_TYPES = {
    EventType.CUSTOMER_ORDER,
    EventType.ORDER_RECEIVED,
    EventType.DELIVERY_UPDATE,
}


async def handle_order(event: BusinessEvent, org_id: str, repo) -> str | None:
    """
    If the event is order-related, create or update the Order record.
    Returns the order_id if one was created/found, else None.
    """
    if event.event_type not in ORDER_EVENT_TYPES:
        return None

    try:
        entities = event.entities or {}
        order_id = repo.create_order(org_id, {
            "customer_id": event.sender_id,
            "customer_name": entities.get("customer") or entities.get("buyer"),
            "items": entities.get("items") or ([entities.get("item")] if entities.get("item") else []),
            "quantity": entities.get("quantity"),
            "amount": entities.get("amount"),
            "currency": entities.get("currency", "UGX"),
            "source_event_id": event.event_id,
            "source_message": event.raw_message,
            "status": "pending",
            "payment_status": "unpaid",
            "delivery_status": "pending",
        })
        logger.info("order_created", extra={"order_id": order_id, "event_id": event.event_id})
        return order_id
    except Exception as exc:
        logger.error("order_creation_failed", extra={"error": str(exc)})
        return None
