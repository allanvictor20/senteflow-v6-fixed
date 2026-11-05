"""
SenteFlow — Tenancy: which business does a WhatsApp number belong to?
=====================================================================
SenteFlow is the business owner's private assistant. One SenteFlow WhatsApp
number serves many businesses: when a message arrives we look the sender's
phone number up and route it to that business's data. Unknown numbers get no
data at all.

Where the mapping lives
-----------------------
1. Firestore `phone_index/{digits}` → {"org_id": ..., "label": ...}
   Managed from the dashboard via /api/org/phones (org members only).
   Server-only collection: Firestore rules deny all client access.
2. Environment, for single-business deployments and bootstrapping:
   OWNER_PHONES (comma-separated) and/or DEFAULT_OWNER_PHONE map to
   DEFAULT_ORG_ID.

Phone numbers are stored as digits only in international format
("256700000000"). Local Ugandan numbers ("0700 000000") are converted using
DEFAULT_COUNTRY_CODE (default 256).
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from utils.clock import utc_now

logger = logging.getLogger(__name__)

PHONE_INDEX_COLLECTION = "phone_index"
_CACHE_TTL_SECONDS = 60


def normalize_phone(raw: Optional[str]) -> str:
    """'+256 700-000000', '256700000000@s.whatsapp.net', '0700000000' → '256700000000'."""
    if not raw:
        return ""
    value = str(raw).split("@")[0].split(":")[0]
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    country = re.sub(r"\D", "", os.environ.get("DEFAULT_COUNTRY_CODE", "256")) or "256"
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = country + digits[1:]
    return digits


def to_whatsapp_jid(phone: str) -> str:
    digits = normalize_phone(phone)
    return f"{digits}@s.whatsapp.net" if digits else ""


def _env_owner_phones() -> list[str]:
    raw = ",".join(
        part for part in (os.environ.get("OWNER_PHONES", ""), os.environ.get("DEFAULT_OWNER_PHONE", "")) if part
    )
    seen: list[str] = []
    for item in raw.split(","):
        digits = normalize_phone(item)
        if digits and digits not in seen:
            seen.append(digits)
    return seen


class PhoneAlreadyLinked(Exception):
    def __init__(self, phone: str, org_id: str):
        super().__init__(f"{phone} is already linked to another business")
        self.phone = phone
        self.org_id = org_id


class TenantDirectory:
    """Maps WhatsApp numbers to organisations."""

    def __init__(self, db, default_org_id: str):
        self._db = db
        self.default_org_id = default_org_id
        self._cache: dict[str, tuple[float, Optional[str]]] = {}

    # ── Lookups ───────────────────────────────────────────────────────────────

    def org_for_phone(self, raw_phone: str) -> Optional[str]:
        phone = normalize_phone(raw_phone)
        if not phone:
            return None
        if phone in _env_owner_phones():
            return self.default_org_id

        cached = self._cache.get(phone)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

        org_id: Optional[str] = None
        if self._db is not None:
            try:
                doc = self._db.collection(PHONE_INDEX_COLLECTION).document(phone).get()
                if doc.exists:
                    org_id = (doc.to_dict() or {}).get("org_id")
            except Exception as exc:
                # Fail closed: an unreachable index must never expose data.
                logger.error("phone_index_lookup_failed", extra={"error": str(exc)})
                return None
        self._cache[phone] = (time.monotonic(), org_id)
        return org_id

    def owner_phones(self, org_id: str) -> list[str]:
        phones: list[str] = []
        if org_id == self.default_org_id:
            phones.extend(_env_owner_phones())
        if self._db is not None:
            try:
                docs = (
                    self._db.collection(PHONE_INDEX_COLLECTION)
                    .where("org_id", "==", org_id)
                    .get()
                )
                for d in docs:
                    if d.id not in phones:
                        phones.append(d.id)
            except Exception as exc:
                logger.warning("phone_index_owner_list_failed", extra={"error": str(exc), "org_id": org_id})
        return phones

    def list_org_ids(self) -> list[str]:
        orgs: list[str] = []
        if _env_owner_phones():
            orgs.append(self.default_org_id)
        if self._db is not None:
            try:
                for d in self._db.collection(PHONE_INDEX_COLLECTION).get():
                    org = (d.to_dict() or {}).get("org_id")
                    if org and org not in orgs:
                        orgs.append(org)
            except Exception as exc:
                logger.warning("phone_index_org_list_failed", extra={"error": str(exc)})
        return orgs

    # ── Management (dashboard) ────────────────────────────────────────────────

    def link_phone(self, org_id: str, raw_phone: str, label: str = "", added_by: str = "") -> str:
        phone = normalize_phone(raw_phone)
        if len(phone) < 9:
            raise ValueError("Enter the number in international format, e.g. +256700000000")
        ref = self._db.collection(PHONE_INDEX_COLLECTION).document(phone)
        existing = ref.get()
        if existing.exists:
            current = (existing.to_dict() or {}).get("org_id")
            if current and current != org_id:
                raise PhoneAlreadyLinked(phone, current)
        ref.set({
            "phone": phone,
            "org_id": org_id,
            "label": label,
            "added_by": added_by,
            "created_at": utc_now().isoformat(),
        }, merge=True)
        self._cache.pop(phone, None)
        return phone

    def unlink_phone(self, org_id: str, raw_phone: str) -> bool:
        phone = normalize_phone(raw_phone)
        ref = self._db.collection(PHONE_INDEX_COLLECTION).document(phone)
        doc = ref.get()
        if not doc.exists or (doc.to_dict() or {}).get("org_id") != org_id:
            return False
        ref.delete()
        self._cache.pop(phone, None)
        return True

    def list_phones(self, org_id: str) -> list[dict]:
        docs = self._db.collection(PHONE_INDEX_COLLECTION).where("org_id", "==", org_id).get()
        return [{**(d.to_dict() or {}), "phone": d.id} for d in docs]
