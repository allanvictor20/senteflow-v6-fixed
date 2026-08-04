"""
SenteFlow AI — BusinessTask
=============================
Auto-generated actionable items derived from BusinessEvents.

The task engine converts conversational signals into concrete to-dos
the business owner must act on — without them having to track anything manually.
"""



from enum import Enum
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field
from utils.clock import utc_now


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISMISSED = "dismissed"
    OVERDUE = "overdue"


class TaskCategory(str, Enum):
    FOLLOW_UP = "follow_up"
    PAYMENT_COLLECTION = "payment_collection"
    DELIVERY = "delivery"
    INVENTORY = "inventory"
    SUPPLIER = "supplier"
    CUSTOMER_SERVICE = "customer_service"
    ADMIN = "admin"


class BusinessTask(BaseModel):
    """
    A single actionable item for the business owner.
    Generated automatically from incoming BusinessEvents.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str

    title: str
    description: str = ""

    category: TaskCategory = TaskCategory.ADMIN
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING

    # Context links
    source_event_id: Optional[str] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    order_id: Optional[str] = None

    # Scheduling
    due_date: Optional[str] = None       # ISO date or human string
    reminder_sent: bool = False

    # History
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None
    notes: list[str] = Field(default_factory=list)

    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    def complete(self, by: str = "system") -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = utc_now().isoformat()
        self.completed_by = by
        self.updated_at = utc_now().isoformat()

    def dismiss(self) -> None:
        self.status = TaskStatus.DISMISSED
        self.updated_at = utc_now().isoformat()

    def mark_overdue(self) -> None:
        if self.status == TaskStatus.PENDING:
            self.status = TaskStatus.OVERDUE
            self.updated_at = utc_now().isoformat()

    def is_active(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE)


# ── Task Templates ─────────────────────────────────────────────────────────────
# Called by TaskGenerationService to produce consistent task titles.

def make_follow_up_task(
    org_id: str,
    customer_name: str,
    customer_id: str,
    due_date: Optional[str],
    source_event_id: Optional[str] = None,
    order_id: Optional[str] = None,
) -> BusinessTask:
    return BusinessTask(
        org_id=org_id,
        title=f"Follow up {customer_name}",
        description=f"{customer_name} promised payment. Follow up on {due_date or 'soon'}.",
        category=TaskCategory.PAYMENT_COLLECTION,
        priority=TaskPriority.HIGH,
        customer_id=customer_id,
        customer_name=customer_name,
        order_id=order_id,
        source_event_id=source_event_id,
        due_date=due_date,
    )


def make_restock_task(
    org_id: str,
    item: str,
    source_event_id: Optional[str] = None,
) -> BusinessTask:
    return BusinessTask(
        org_id=org_id,
        title=f"Restock {item}",
        description=f"Inventory low for {item}. Place a supplier order soon.",
        category=TaskCategory.INVENTORY,
        priority=TaskPriority.HIGH,
        source_event_id=source_event_id,
    )


def make_delivery_task(
    org_id: str,
    customer_name: str,
    customer_id: str,
    order_id: Optional[str],
    delivery_date: Optional[str],
    source_event_id: Optional[str] = None,
) -> BusinessTask:
    return BusinessTask(
        org_id=org_id,
        title=f"Prepare delivery for {customer_name}",
        description=f"Paid order ready to dispatch. Delivery: {delivery_date or 'ASAP'}.",
        category=TaskCategory.DELIVERY,
        priority=TaskPriority.HIGH,
        customer_id=customer_id,
        customer_name=customer_name,
        order_id=order_id,
        source_event_id=source_event_id,
        due_date=delivery_date,
    )


def make_confirm_order_task(
    org_id: str,
    customer_name: str,
    customer_id: str,
    order_id: Optional[str],
    source_event_id: Optional[str] = None,
) -> BusinessTask:
    return BusinessTask(
        org_id=org_id,
        title=f"Confirm order from {customer_name}",
        description=f"New order from {customer_name} needs confirmation.",
        category=TaskCategory.CUSTOMER_SERVICE,
        priority=TaskPriority.MEDIUM,
        customer_id=customer_id,
        customer_name=customer_name,
        order_id=order_id,
        source_event_id=source_event_id,
    )
