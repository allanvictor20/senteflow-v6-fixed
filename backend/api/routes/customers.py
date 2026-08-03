"""
SenteFlow AI — Customer Profile Routes
========================================
GET  /api/customers                    — list all customer profiles
GET  /api/customers/{customer_id}      — get one profile
GET  /api/customers/{customer_id}/summary  — AI-generated narrative
"""

import logging
from fastapi import APIRouter, Depends, HTTPException

from core.auth import verify_firebase_token, ensure_org_access

logger = logging.getLogger(__name__)

customers_router = APIRouter(prefix="/api/customers", tags=["customers"])

_profile_repo = None
_customer_memory_svc = None


def set_customer_dependencies(profile_repo, customer_memory_svc):
    global _profile_repo, _customer_memory_svc
    _profile_repo = profile_repo
    _customer_memory_svc = customer_memory_svc


@customers_router.get("")
async def list_customers(
    org_id: str,
    limit: int = 100,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    if not _profile_repo:
        raise HTTPException(500, "Customer repository not initialised")
    profiles = _profile_repo.list(org_id, limit=limit)
    return {"customers": [p.model_dump(mode="json") for p in profiles]}


@customers_router.get("/{customer_id}")
async def get_customer(
    customer_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    profile = _profile_repo.get(org_id, customer_id)
    if not profile:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return profile.model_dump(mode="json")


@customers_router.get("/{customer_id}/summary")
async def get_customer_summary(
    customer_id: str,
    org_id: str,
    _token: dict = Depends(verify_firebase_token),
):
    ensure_org_access(_token, org_id)
    if not _customer_memory_svc:
        raise HTTPException(500, "CustomerMemoryService not initialised")
    summary = await _customer_memory_svc.refresh_ai_summary(org_id, customer_id)
    profile = _profile_repo.get(org_id, customer_id)
    if not profile:
        raise HTTPException(404, "Customer not found")
    return {"customer_id": customer_id, "summary": summary or profile.ai_summary or "No summary available yet."}
