"""
SenteFlow — Debt Domain
========================
A Debt represents money owed by a customer to the business.
It is created from a debt_created or payment_promise BusinessEvent.
When a payment_received event arrives, the matching debt is reduced or resolved.
"""

import uuid
from datetime import datetime

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from utils.clock import utc_now


class DebtStatus(str, Enum):
    OPEN       = "open"
    PARTIAL    = "partial"      # some payment received
    RESOLVED   = "resolved"     # fully paid
    OVERDUE    = "overdue"      # due date passed, still unpaid
    WRITTEN_OFF = "written_off"


class Debt(BaseModel):
    """Tracks a single credit event between the business and a customer."""

    debt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    customer_id: str
    customer_name: Optional[str] = None

    original_amount: float
    outstanding_amount: float
    currency: str = "UGX"

    due_date: Optional[str] = None
    status: DebtStatus = DebtStatus.OPEN

    source_event_id: str = ""
    source_message: str = ""
    notes: str = ""

    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())

    def apply_payment(self, amount: float) -> "Debt":
        """Return a new Debt with the payment applied."""
        remaining = max(0.0, self.outstanding_amount - amount)
        status = DebtStatus.RESOLVED if remaining == 0 else DebtStatus.PARTIAL
        return self.model_copy(update={
            "outstanding_amount": remaining,
            "status": status,
            "updated_at": utc_now().isoformat(),
        })

    def is_overdue(self) -> bool:
        if not self.due_date:
            return False
        try:
            return datetime.fromisoformat(self.due_date) < utc_now()
        except ValueError:
            return False
