"""Event-first extraction workflow for text and media-derived messages."""

from domain.events.business_event import BusinessEvent
from services.llm.event_extractor import extract_event


async def run_event_extraction(
    text: str,
    org_id: str,
    sender_id: str,
    context: dict | None = None,
    source_type: str = "whatsapp",
) -> BusinessEvent:
    return await extract_event(
        raw_message=text,
        sender_id=sender_id,
        business_id=org_id,
        context={**(context or {}), "source_type": source_type},
    )