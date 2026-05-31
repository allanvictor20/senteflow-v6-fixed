"""
SenteFlow AI — Customer Domain Model
=======================================
Customers are first-class citizens built up over time from interactions.
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


class Customer(BaseModel):
    customer_id: str
    org_id: str
    display_name: str
    phone_number: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    outstanding_balance: float = 0.0
    total_paid: float = 0.0
    total_owed: float = 0.0
    usual_items: list[str] = Field(default_factory=list)
    payment_behavior: str = "unknown"
    last_interaction: Optional[str] = None
    risk_level: str = "unknown"
    notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)