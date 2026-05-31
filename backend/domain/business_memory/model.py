"""
SenteFlow AI - Business Memory
==============================
Customer knowledge accumulated across WhatsApp conversations.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CustomerMemory(BaseModel):
    customer_id: str
    org_id: str
    display_name: str
    phone_number: Optional[str] = None
    total_orders: int = 0
    total_paid: float = 0.0
    total_outstanding: float = 0.0
    average_order_value: float = 0.0
    typical_payment_days: int = 0
    payment_reliability: str = "unknown"
    frequently_ordered: list[str] = Field(default_factory=list)
    last_order_items: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    first_contact: Optional[str] = None
    last_contact: Optional[str] = None
    contact_frequency_days: Optional[float] = None
    relationship_score: float = 0.0
    risk_level: str = "unknown"
    notes: list[str] = Field(default_factory=list)
    last_conversation_stage: str = "unknown"
    open_promises: list[dict] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_ai_context(self) -> str:
        parts = [f"Customer: {self.display_name}"]
        if self.total_orders > 0:
            parts.append(f"Orders: {self.total_orders}")
        if self.total_paid > 0:
            parts.append(f"Total paid: UGX {self.total_paid:,.0f}")
        if self.total_outstanding > 0:
            parts.append(f"Outstanding: UGX {self.total_outstanding:,.0f}")
        if self.payment_reliability != "unknown":
            parts.append(f"Payment: {self.payment_reliability}")
        if self.frequently_ordered:
            parts.append(f"Usually buys: {', '.join(self.frequently_ordered[:3])}")
        if self.open_promises:
            parts.append(f"Open promises: {len(self.open_promises)}")
        return " | ".join(parts)
