"""
SenteFlow AI — TaskGenerationService
======================================
Turns BusinessEvents into concrete to-dos for the owner.

The task templates already lived in domain/debts/task.py and the /api/tasks
router already read the resulting collection, but nothing ever wrote to it —
this service is the missing link between the two.

One event produces at most one task. Events that carry no obligation
(business notes, inventory counts, unknown) produce none.
"""

import logging
from typing import Optional

from domain.debts.task import (
    BusinessTask,
    TaskCategory,
    TaskPriority,
    make_confirm_order_task,
    make_delivery_task,
    make_follow_up_task,
    make_restock_task,
)
from domain.events.event_types import EventType

logger = logging.getLogger(__name__)


class TaskGenerationService:

    def __init__(self, task_repo):
        self._repo = task_repo

    def generate_from_event(
        self,
        org_id: str,
        event,
        order_id: Optional[str] = None,
    ) -> list[BusinessTask]:
        """
        Build and persist the tasks implied by `event`.

        Returns the tasks that were created (empty when the event implies none).
        """
        entities = event.entities or {}
        customer_name = (
            entities.get("customer")
            or entities.get("payer")
            or entities.get("debtor")
            or entities.get("buyer")
            or event.sender_id
        )
        order_id = order_id or entities.get("order_id")
        tasks: list[BusinessTask] = []

        if event.event_type in (EventType.PAYMENT_PROMISE, EventType.DEBT_CREATED):
            tasks.append(make_follow_up_task(
                org_id=org_id,
                customer_name=customer_name,
                customer_id=event.sender_id,
                due_date=entities.get("due_date") or entities.get("date"),
                source_event_id=event.event_id,
                order_id=order_id,
            ))

        elif event.event_type == EventType.LOW_STOCK_ALERT:
            tasks.append(make_restock_task(
                org_id=org_id,
                item=entities.get("item") or "stock",
                source_event_id=event.event_id,
            ))

        elif event.event_type in (EventType.CUSTOMER_ORDER, EventType.ORDER_RECEIVED):
            tasks.append(make_confirm_order_task(
                org_id=org_id,
                customer_name=customer_name,
                customer_id=event.sender_id,
                order_id=order_id,
                source_event_id=event.event_id,
            ))

        elif event.event_type == EventType.PAYMENT_RECEIVED:
            tasks.append(make_delivery_task(
                org_id=org_id,
                customer_name=customer_name,
                customer_id=event.sender_id,
                order_id=order_id,
                delivery_date=entities.get("delivery_date"),
                source_event_id=event.event_id,
            ))

        elif event.event_type == EventType.COMPLAINT:
            task = BusinessTask(
                org_id=org_id,
                title=f"Resolve complaint from {customer_name}",
                description=event.raw_message[:200] or "Customer raised a complaint.",
                category=TaskCategory.CUSTOMER_SERVICE,
                priority=TaskPriority.URGENT,
                customer_id=event.sender_id,
                customer_name=customer_name,
                source_event_id=event.event_id,
            )
            tasks.append(task)

        elif event.event_type in (EventType.FOLLOW_UP_REQUIRED, EventType.REMINDER_REQUEST):
            tasks.append(make_follow_up_task(
                org_id=org_id,
                customer_name=customer_name,
                customer_id=event.sender_id,
                due_date=entities.get("due_date"),
                source_event_id=event.event_id,
                order_id=order_id,
            ))

        for task in tasks:
            try:
                self._repo.save(org_id, task)
            except Exception as exc:
                logger.warning(
                    "task_persist_failed",
                    extra={"task": task.title, "error": str(exc)},
                )

        if tasks:
            logger.info(
                "tasks_generated",
                extra={
                    "count": len(tasks),
                    "event_type": event.event_type.value,
                    "org_id": org_id,
                },
            )
        return tasks
