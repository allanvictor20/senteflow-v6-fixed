"""
SenteFlow — per-business settings read on the hot path
======================================================
The business profile (name, type, currency, credit policy, ...) is edited in
the dashboard (Settings → Business profile, PUT /api/org/profile) and stored at
organizations/{org_id}/profile/config. The extraction prompt reads the whole
profile; replies read the default currency through here.
"""

from __future__ import annotations

import time
from typing import Optional

SUPPORTED_CURRENCIES = ("UGX", "KES", "TZS", "RWF", "USD")
DEFAULT_CURRENCY = "UGX"

_TTL_SECONDS = 60
_cache: dict[str, tuple[float, str]] = {}


def invalidate(org_id: str) -> None:
    _cache.pop(org_id, None)


def _remember(org_id: str, currency: Optional[str]) -> str:
    value = currency if currency in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY
    _cache[org_id] = (time.monotonic(), value)
    return value


def _cached(org_id: str) -> Optional[str]:
    hit = _cache.get(org_id)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return None


async def business_currency(profile_repo, org_id: str) -> str:
    """Default currency for this business (never raises)."""
    cached = _cached(org_id)
    if cached:
        return cached
    if profile_repo is None:
        return DEFAULT_CURRENCY
    try:
        profile = await profile_repo.get_profile(org_id)
    except Exception:
        profile = None
    return _remember(org_id, getattr(profile, "currency", None))


def business_currency_sync(db, org_id: str) -> str:
    """Same, for synchronous callers (briefing, reminders)."""
    cached = _cached(org_id)
    if cached:
        return cached
    try:
        doc = (
            db.collection("organizations").document(org_id)
            .collection("profile").document("config").get()
        )
        data = doc.to_dict() if doc.exists else {}
    except Exception:
        data = {}
    return _remember(org_id, (data or {}).get("currency"))
