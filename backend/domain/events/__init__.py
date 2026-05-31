"""
SenteFlow — Event Domain
========================
The central abstraction: every WhatsApp message becomes a BusinessEvent.
"""
from domain.events.business_event import BusinessEvent, EventResult, ProcessingStatus
from domain.events.event_types import (
    EventType,
    FINANCIAL_EVENTS,
    CUSTOMER_EVENTS,
    INVENTORY_EVENTS,
    FOLLOWUP_REQUIRED_EVENTS,
)

__all__ = [
    "BusinessEvent", "EventResult", "ProcessingStatus",
    "EventType", "FINANCIAL_EVENTS", "CUSTOMER_EVENTS",
    "INVENTORY_EVENTS", "FOLLOWUP_REQUIRED_EVENTS",
]
