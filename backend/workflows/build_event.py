"""
SenteFlow — BuildEventWorkflow
================================
Wraps a raw message + modality into a BusinessEvent.
Used by the extraction pipeline and the message router.
"""

from typing import Optional
from domain.events import BusinessEvent, EventType, ProcessingStatus


async def build_event(
    text: str,
    sender_id: str,
    org_id: str,
    context: Optional[dict] = None,
    source_type: str = "whatsapp",
) -> BusinessEvent:
    """Convert raw text into a BusinessEvent via the LLM extractor."""
    from services.llm.event_extractor import extract_event
    event = await extract_event(
        raw_message=text,
        sender_id=sender_id,
        business_id=org_id,
        context={**(context or {}), "source_type": source_type},
    )
    event.processing_status = ProcessingStatus.INTERPRETED
    return event
