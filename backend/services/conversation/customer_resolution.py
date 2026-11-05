"""
SenteFlow — resolve which customer a message is about
=====================================================
The sender of every WhatsApp message is the business owner. The customer is
whoever the owner is talking ABOUT ("Brian paid 50k"), so it has to be read
from the extracted entities and matched against the business's customer list.

Outcomes:
  - matched an existing customer          → use their id
  - several plausible matches             → ask the owner which one
  - nobody matches                        → create a new customer
  - the event is not about a customer     → nothing to resolve
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from domain.events.event_types import EventType
from services.conversation.entity_resolver import EntityResolver
from services.tenancy import normalize_phone

logger = logging.getLogger(__name__)

# Which entity names the customer for each event type, in order of preference.
_ROLE_KEYS: dict[EventType, tuple[str, ...]] = {
    EventType.PAYMENT_RECEIVED: ("customer", "payer", "sender"),
    EventType.PAYMENT_PROMISE: ("customer", "debtor", "payer"),
    EventType.DEBT_CREATED: ("customer", "debtor", "buyer"),
    EventType.CUSTOMER_ORDER: ("customer", "buyer"),
    EventType.ORDER_RECEIVED: ("customer", "buyer"),
    EventType.CUSTOMER_INQUIRY: ("customer",),
    EventType.NEGOTIATION: ("customer",),
    EventType.COMPLAINT: ("customer",),
    EventType.APPOINTMENT_REQUEST: ("customer",),
    EventType.DELIVERY_UPDATE: ("customer", "buyer"),
    EventType.FOLLOW_UP_REQUIRED: ("customer", "debtor", "person"),
    EventType.REMINDER_REQUEST: ("customer", "debtor", "person"),
    EventType.PAYMENT: ("customer", "payer"),
    EventType.INCOME: ("customer", "payer"),
}

_NOT_A_NAME = {"", "unknown", "someone", "customer", "client", "he", "she", "they", "him", "her", "me", "i"}


@dataclass
class CustomerResolution:
    customer_id: Optional[str] = None
    display_name: Optional[str] = None
    is_new: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""
    candidates: list[dict] = field(default_factory=list)


def customer_name_from_entities(event_type: EventType, entities: dict) -> Optional[str]:
    for key in _ROLE_KEYS.get(event_type, ()):
        value = entities.get(key)
        if isinstance(value, str) and value.strip().lower() not in _NOT_A_NAME:
            return value.strip()
    return None


class CustomerResolver:
    def __init__(self, repo, org_id: str):
        self.repo = repo
        self.org_id = org_id

    def resolve_name(self, name: str, phone: Optional[str] = None, create_if_new: bool = True) -> CustomerResolution:
        name = (name or "").strip()
        if not name:
            return CustomerResolution()

        # A phone number given by the owner is the strongest identifier.
        digits = normalize_phone(phone) if phone else ""
        if digits and hasattr(self.repo, "get_by_phone"):
            try:
                by_phone = self.repo.get_by_phone(self.org_id, digits)
            except Exception:
                by_phone = None
            if by_phone and "@" not in by_phone.get("id", ""):
                return CustomerResolution(customer_id=by_phone["id"], display_name=by_phone.get("display_name") or name)

        result = EntityResolver(self.repo, self.org_id).resolve_customer(name)
        if result.get("resolved") and result.get("customer"):
            customer = result["customer"]
            return CustomerResolution(customer_id=customer["id"], display_name=customer.get("display_name") or name)

        if result.get("needs_clarification"):
            question = result.get("clarification_question") or f"Which {name} do you mean?"
            question += " Reply with their full name (or say 'new' plus the name if it's someone new)."
            return CustomerResolution(
                needs_clarification=True,
                clarification_question=question,
                candidates=result.get("candidates") or [],
            )

        if not create_if_new:
            return CustomerResolution()

        try:
            created = self.repo.create_customer(self.org_id, display_name=name, phone_number=digits or None)
        except Exception as exc:
            logger.warning("customer_create_failed", extra={"error": str(exc)})
            return CustomerResolution(display_name=name)
        logger.info("customer_created", extra={"customer_id": created["id"]})
        return CustomerResolution(customer_id=created["id"], display_name=name, is_new=True)

    def resolve_for_event(self, event) -> CustomerResolution:
        """Resolve the customer of a BusinessEvent and stamp it onto the event."""
        entities = event.entities or {}
        name = customer_name_from_entities(event.event_type, entities)
        if not name:
            return CustomerResolution()

        # "new Brian Okello" after a "which Brian?" question → force a new customer.
        force_new = name.lower().startswith("new ")
        if force_new:
            name = name[4:].strip()

        if force_new:
            try:
                created = self.repo.create_customer(self.org_id, display_name=name, phone_number=normalize_phone(entities.get("phone")) or None)
                resolution = CustomerResolution(customer_id=created["id"], display_name=name, is_new=True)
            except Exception as exc:
                logger.warning("customer_create_failed", extra={"error": str(exc)})
                resolution = CustomerResolution(display_name=name)
        else:
            resolution = self.resolve_name(name, phone=entities.get("phone"))

        if resolution.customer_id:
            event.customer_id = resolution.customer_id
            event.customer_name = resolution.display_name
            entities["customer_id"] = resolution.customer_id
            entities.setdefault("customer", resolution.display_name)
            event.entities = entities
        return resolution
