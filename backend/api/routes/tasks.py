"""
SenteFlow AI — Task Routes
============================
GET  /api/tasks                  — list active tasks
GET  /api/tasks/overdue          — list overdue tasks
POST /api/tasks/{task_id}/complete
POST /api/tasks/{task_id}/dismiss
"""

import logging
from fastapi import APIRouter, Depends, HTTPException

from core.auth import verify_firebase_token, verify_org_access

logger = logging.getLogger(__name__)

tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_task_repo = None


def set_task_dependencies(task_repo):
    global _task_repo
    _task_repo = task_repo


@tasks_router.get("")
async def list_tasks(
    org_id: str,
    limit: int = 100,
    _token: dict = Depends(verify_firebase_token),
):
    verify_org_access(_token, org_id)
    if not _task_repo:
        raise HTTPException(500, "Task repository not initialised")
    tasks = _task_repo.list_active(org_id, limit=limit)
    return {"tasks": [t.model_dump(mode="json") for t in tasks]}


@tasks_router.get("/overdue")
async def list_overdue_tasks(
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    verify_org_access(_token, org_id)
    tasks = _task_repo.list_overdue(org_id)
    return {"tasks": [t.model_dump(mode="json") for t in tasks]}


@tasks_router.get("/customer/{customer_id}")
async def list_customer_tasks(
    customer_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    verify_org_access(_token, org_id)
    tasks = _task_repo.list_by_customer(org_id, customer_id)
    return {"tasks": [t.model_dump(mode="json") for t in tasks]}


@tasks_router.post("/{task_id}/complete")
async def complete_task(
    task_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    verify_org_access(_token, org_id)
    _task_repo.complete_task(org_id, task_id, by="user")
    return {"status": "completed", "task_id": task_id}


@tasks_router.post("/{task_id}/dismiss")
async def dismiss_task(
    task_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    verify_org_access(_token, org_id)
    task = _task_repo.get(org_id, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    task.dismiss()
    _task_repo.save(org_id, task)
    return {"status": "dismissed", "task_id": task_id}
