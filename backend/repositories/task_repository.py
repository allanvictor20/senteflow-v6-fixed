"""
SenteFlow AI — Task Repository
=================================
Firestore persistence for BusinessTasks.
"""

import logging


from typing import Optional

from domain.debts.task import BusinessTask, TaskStatus
from utils.clock import utc_now

logger = logging.getLogger(__name__)


class TaskRepository:

    def __init__(self, db):
        self._db = db

    def _col(self, org_id: str):
        return (
            self._db.collection("organizations")
            .document(org_id)
            .collection("tasks")
        )

    def save(self, org_id: str, task: BusinessTask) -> str:
        task.org_id = org_id
        task.updated_at = utc_now().isoformat()
        self._col(org_id).document(task.id).set(task.model_dump(mode="json"), merge=True)
        logger.debug("task_saved", extra={"task_id": task.id, "title": task.title})
        return task.id

    def get(self, org_id: str, task_id: str) -> Optional[BusinessTask]:
        doc = self._col(org_id).document(task_id).get()
        if not doc.exists:
            return None
        return BusinessTask(**doc.to_dict())

    def list_active(self, org_id: str, limit: int = 100) -> list[BusinessTask]:
        docs = (
            self._col(org_id)
            .where("status", "in", [TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value, TaskStatus.OVERDUE.value])
            .order_by("due_date")
            .limit(limit)
            .get()
        )
        tasks = []
        for d in docs:
            try:
                tasks.append(BusinessTask(**d.to_dict()))
            except Exception as e:
                logger.warning("task_parse_error", extra={"id": d.id, "error": str(e)})
        return tasks

    def list_by_customer(self, org_id: str, customer_id: str) -> list[BusinessTask]:
        docs = (
            self._col(org_id)
            .where("customer_id", "==", customer_id)
            .order_by("created_at", direction="DESCENDING")
            .limit(50)
            .get()
        )
        return [BusinessTask(**d.to_dict()) for d in docs]

    def complete_task(self, org_id: str, task_id: str, by: str = "system") -> None:
        self._col(org_id).document(task_id).update({
            "status": TaskStatus.COMPLETED.value,
            "completed_at": utc_now().isoformat(),
            "completed_by": by,
            "updated_at": utc_now().isoformat(),
        })

    def list_overdue(self, org_id: str) -> list[BusinessTask]:
        now = utc_now().isoformat()
        docs = (
            self._col(org_id)
            .where("status", "==", TaskStatus.PENDING.value)
            .where("due_date", "<", now)
            .limit(200)
            .get()
        )
        return [BusinessTask(**d.to_dict()) for d in docs]
