"""
SenteFlow — UpdateMemoryWorkflow
==================================
Called after every BusinessEvent is processed.
Keeps CustomerMemory current so the next interaction is richer.
"""

import logging
from domain.events import BusinessEvent

logger = logging.getLogger(__name__)


async def update_memory(event: BusinessEvent, org_id: str, db) -> None:
    """Update CustomerMemory from a processed BusinessEvent."""
    from repositories.memory_repository import MemoryRepository
    from repositories.customer_repository import CustomerRepository
    from services.memory.memory_engine import update_from_event

    try:
        mem_repo = MemoryRepository(db)
        cust_repo = CustomerRepository(db)
        await update_from_event(event.model_dump(mode="json"), org_id, mem_repo, cust_repo)
        logger.info("memory_updated", extra={"event_id": event.event_id, "sender": event.sender_id})
    except Exception as exc:
        logger.error("memory_update_failed", extra={"error": str(exc), "event_id": event.event_id})
