"""
SenteFlow AI — Order Domain Model
===================================
Orders are first-class domain objects, not just events.

Every WhatsApp-originated purchase flows through an Order lifecycle
with state transitions, an event timeline, and payment/delivery tracking.
"""



from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field
from utils.clock import utc_now


class OrderStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PREPARING = "preparing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"


class DeliveryStatus(str, Enum):
    NOT_SCHEDULED = "not_scheduled"
    SCHEDULED = "scheduled"
    PREPARING = "preparing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"


class OrderItem(BaseModel):
    name: str
    quantity: float = 1.0
    unit: str = "units"
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    notes: Optional[str] = None


class OrderTimelineEntry(BaseModel):
    status: str
    note: str = ""
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())
    actor: str = "system"   # "customer" | "business" | "system"


class Order(BaseModel):
    """
    Full lifecycle representation of a customer order.
    Created from a CUSTOMER_ORDER event and updated by subsequent events.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    customer_id: str
    customer_name: Optional[str] = None

    # State
    status: OrderStatus = OrderStatus.DRAFT
    payment_status: PaymentStatus = PaymentStatus.UNPAID
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_SCHEDULED

    # Line items
    items: list[OrderItem] = Field(default_factory=list)

    # Financials
    subtotal: float = 0.0
    discount: float = 0.0
    total: float = 0.0
    amount_paid: float = 0.0
    debt_amount: float = 0.0

    # Promises / scheduling
    promised_payment_date: Optional[str] = None
    delivery_date: Optional[str] = None
    delivery_address: Optional[str] = None

    # Originating event
    source_event_id: Optional[str] = None
    raw_message: Optional[str] = None

    # Timeline
    timeline: list[OrderTimelineEntry] = Field(default_factory=list)

    # Notes / tags
    notes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def add_timeline_entry(self, status: str, note: str = "", actor: str = "system") -> None:
        self.timeline.append(OrderTimelineEntry(status=status, note=note, actor=actor))
        self.updated_at = utc_now().isoformat()

    def confirm(self) -> None:
        self.status = OrderStatus.CONFIRMED
        self.add_timeline_entry("confirmed", "Order confirmed")

    def mark_awaiting_payment(self, promised_date: Optional[str] = None) -> None:
        self.status = OrderStatus.AWAITING_PAYMENT
        self.payment_status = PaymentStatus.UNPAID
        if promised_date:
            self.promised_payment_date = promised_date
        self.add_timeline_entry("awaiting_payment", "Awaiting payment")

    def record_payment(self, amount: float) -> None:
        self.amount_paid += amount
        self.debt_amount = max(0.0, self.total - self.amount_paid)
        if self.debt_amount <= 0:
            self.payment_status = PaymentStatus.PAID
            self.status = OrderStatus.PAID
            self.add_timeline_entry("paid", f"Payment received: UGX {amount:,.0f}", actor="customer")
        else:
            self.payment_status = PaymentStatus.PARTIAL
            self.add_timeline_entry("partial_payment", f"Partial payment: UGX {amount:,.0f}", actor="customer")

    def dispatch(self, delivery_date: Optional[str] = None) -> None:
        self.status = OrderStatus.DISPATCHED
        self.delivery_status = DeliveryStatus.DISPATCHED
        if delivery_date:
            self.delivery_date = delivery_date
        self.add_timeline_entry("dispatched", "Order dispatched")

    def mark_delivered(self) -> None:
        self.status = OrderStatus.DELIVERED
        self.delivery_status = DeliveryStatus.DELIVERED
        self.add_timeline_entry("delivered", "Order delivered", actor="business")

    def cancel(self, reason: str = "") -> None:
        self.status = OrderStatus.CANCELLED
        self.add_timeline_entry("cancelled", reason or "Order cancelled")

    def compute_totals(self) -> None:
        self.subtotal = sum(
            (item.total_price or (item.unit_price or 0) * item.quantity)
            for item in self.items
        )
        self.total = max(0.0, self.subtotal - self.discount)
        self.debt_amount = max(0.0, self.total - self.amount_paid)

    def to_summary(self) -> str:
        item_names = ", ".join(i.name for i in self.items[:3])
        return (
            f"Order #{self.id[:8]} | {self.customer_name or self.customer_id} | "
            f"{item_names} | UGX {self.total:,.0f} | {self.status.value}"
        )
