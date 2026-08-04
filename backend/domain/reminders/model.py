"""
SenteFlow AI — Reminder Domain Model
"""



from typing import Optional
from pydantic import BaseModel, Field
import uuid
from utils.clock import utc_now


class Reminder(BaseModel):
    reminder_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    sender_id: str
    target_person: Optional[str] = None
    message: str = ""
    due_date: Optional[str] = None
    due_date_display: str = "soon"
    amount: Optional[float] = None
    currency: str = "UGX"
    status: str = "pending"
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    notified_at: Optional[str] = None
