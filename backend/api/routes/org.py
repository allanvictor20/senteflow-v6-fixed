"""
SenteFlow AI — Business settings: linked WhatsApp numbers
==========================================================
Only numbers linked here (or listed in OWNER_PHONES / DEFAULT_OWNER_PHONE
for the default business) can talk to SenteFlow on WhatsApp. Messages from
any other number get no data.

GET    /api/org/phones?org_id=...           list linked numbers
POST   /api/org/phones?org_id=...           {"phone": "+2567...", "label": "Owner"}
DELETE /api/org/phones/{phone}?org_id=...

Business profile (read by the AI on every message — see BusinessProfile):

GET    /api/org/profile?org_id=...
PUT    /api/org/profile?org_id=...          any of the editable fields below
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import verify_firebase_token, require_org, token_identity

org_router = APIRouter(prefix="/api/org", tags=["org"])

_tenants = None
_profile_repo = None


def set_org_dependencies(tenants, profile_repo=None):
    global _tenants, _profile_repo
    _tenants = tenants
    _profile_repo = profile_repo


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


# ─── Business profile ─────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    """Fields the owner edits in Settings. Omitted fields are left unchanged."""
    name: Optional[str] = Field(None, max_length=120)
    business_type: Optional[str] = Field(None, max_length=120)
    location: Optional[str] = Field(None, max_length=120)
    owner_name: Optional[str] = Field(None, max_length=120)
    currency: Optional[str] = None
    credit_policy: Optional[str] = Field(None, max_length=500)
    operating_hours: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=1000)


_PROFILE_FIELDS = tuple(ProfileUpdate.model_fields)


def _require_profile_repo():
    if _profile_repo is None:
        raise HTTPException(503, "Business profile store not initialised")
    return _profile_repo


def _profile_out(profile) -> dict:
    data = profile.model_dump(mode="json")
    return {k: data.get(k) for k in ("org_id", *_PROFILE_FIELDS)}


@org_router.get("/profile")
async def get_profile(org_id: str, _token: dict = Depends(verify_firebase_token)):
    from domain.business_profile.model import BusinessProfile

    await require_org(_token, org_id)
    repo = _require_profile_repo()
    profile = await repo.get_profile(org_id) or BusinessProfile(org_id=org_id)
    return _profile_out(profile)


@org_router.put("/profile")
async def update_profile(org_id: str, body: ProfileUpdate, _token: dict = Depends(verify_firebase_token)):
    from domain.business_profile.model import BusinessProfile
    from services.business_settings import SUPPORTED_CURRENCIES, invalidate

    await require_org(_token, org_id)
    repo = _require_profile_repo()

    updates = body.model_dump(exclude_none=True)
    if "currency" in updates:
        updates["currency"] = updates["currency"].strip().upper()
        if updates["currency"] not in SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"Currency must be one of: {', '.join(SUPPORTED_CURRENCIES)}")
    updates = {k: (v.strip() if isinstance(v, str) else v) for k, v in updates.items()}

    current = await repo.get_profile(org_id) or BusinessProfile(org_id=org_id)
    # Keep fields the dashboard doesn't edit (e.g. products) as they are.
    profile = current.model_copy(update={**updates, "org_id": org_id})
    await repo.save_profile(profile)
    invalidate(org_id)
    return _profile_out(profile)
