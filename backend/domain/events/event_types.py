"""
SenteFlow AI - Event Types
==========================
Universal event classification for SME WhatsApp businesses.
Every WhatsApp message becomes one of these event types.
"""

from enum import Enum


class EventType(str, Enum):
    # Customer interaction events
    CUSTOMER_INQUIRY = "customer_inquiry"
    CUSTOMER_ORDER = "customer_order"
    ORDER_RECEIVED = "order_received"
    NEGOTIATION = "negotiation"
    COMPLAINT = "complaint"
    APPOINTMENT_REQUEST = "appointment_request"

    # Financial events
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_PROMISE = "payment_promise"
    DEBT_CREATED = "debt_created"
    EXPENSE_RECORDED = "expense_recorded"

    # Operational events
    DELIVERY_UPDATE = "delivery_update"
    INVENTORY_UPDATE = "inventory_update"
    LOW_STOCK_ALERT = "low_stock_alert"
    SUPPLIER_MESSAGE = "supplier_message"

    # Internal events
    BUSINESS_NOTE = "business_note"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    REMINDER_REQUEST = "reminder_request"

    # System
    UNKNOWN = "unknown"

    # Legacy aliases kept so old Firestore records still resolve.
    PAYMENT = "payment"
    INCOME = "income"


FINANCIAL_EVENTS: frozenset[EventType] = frozenset({
    EventType.PAYMENT_RECEIVED,
    EventType.PAYMENT_PROMISE,
    EventType.DEBT_CREATED,
    EventType.EXPENSE_RECORDED,
    EventType.PAYMENT,
    EventType.INCOME,
})

CUSTOMER_EVENTS: frozenset[EventType] = frozenset({
    EventType.CUSTOMER_INQUIRY,
    EventType.CUSTOMER_ORDER,
    EventType.ORDER_RECEIVED,
    EventType.NEGOTIATION,
    EventType.COMPLAINT,
    EventType.APPOINTMENT_REQUEST,
})

INVENTORY_EVENTS: frozenset[EventType] = frozenset({
    EventType.INVENTORY_UPDATE,
    EventType.LOW_STOCK_ALERT,
})

FOLLOWUP_REQUIRED_EVENTS: frozenset[EventType] = frozenset({
    EventType.PAYMENT_PROMISE,
    EventType.DEBT_CREATED,
    EventType.CUSTOMER_ORDER,
    EventType.SUPPLIER_MESSAGE,
    EventType.LOW_STOCK_ALERT,
    EventType.COMPLAINT,
    EventType.FOLLOW_UP_REQUIRED,
    EventType.REMINDER_REQUEST,
})
