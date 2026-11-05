"""
SenteFlow AI — Business settings: linked WhatsApp numbers
==========================================================
Only numbers linked here (or listed in OWNER_PHONES / DEFAULT_OWNER_PHONE
for the default business) can talk to SenteFlow on WhatsApp. Messages from
any other number get no data.

GET    /api/org/phones?org_id=...           list linked numbers
POST   /api/org/phones?org_id=...           {"phone": "+2567...", "label": "Owner"}
DELETE /api/org/phones/{phone}?org_id=...
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import verify_firebase_token, require_org, token_identity

org_router = APIRouter(prefix="/api/org", tags=["org"])

_tenants = None


def set_org_dependencies(tenants):
    global _tenants
    _tenants = tenants


def _require_tenants():
    if _tenants is None:
        raise HTTPException(503, "Tenant directory not initialised")
    return _tenants


class LinkPhoneRequest(BaseModel):
    phone: str
    label: str = ""


@org_router.get("/phones")
async def list_phones(org_id: str, _token: dict = Depends(verify_firebase_token)):
    await require_org(_token, org_id)
    tenants = _require_tenants()
    phones = await asyncio.to_thread(tenants.list_phones, org_id)
    return {"phones": phones}


@org_router.post("/phones")
async def link_phone(org_id: str, body: LinkPhoneRequest, _token: dict = Depends(verify_firebase_token)):
    from services.tenancy import PhoneAlreadyLinked

    await require_org(_token, org_id)
    tenants = _require_tenants()
    try:
        phone = await asyncio.to_thread(
            tenants.link_phone, org_id, body.phone, body.label, token_identity(_token)
        )
    except PhoneAlreadyLinked:
        raise HTTPException(409, "This number is already linked to another business")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"phone": phone, "org_id": org_id}


@org_router.delete("/phones/{phone}")
async def unlink_phone(phone: str, org_id: str, _token: dict = Depends(verify_firebase_token)):
    await require_org(_token, org_id)
    tenants = _require_tenants()
    removed = await asyncio.to_thread(tenants.unlink_phone, org_id, phone)
    if not removed:
        raise HTTPException(404, "Number not linked to this business")
    return {"removed": True}
