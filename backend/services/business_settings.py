"""
SenteFlow — per-business settings read on the hot path
======================================================
The business profile (name, type, currency, languages, credit policy, ...) is
edited in the dashboard (Settings → Business profile, PUT /api/org/profile)
and stored at organizations/{org_id}/profile/config. The extraction prompt
reads the whole profile; replies, voice transcription and translation read
the few settings below through here (cached for a minute).
"""

from __future__ import annotations

import time
from typing import Optional

SUPPORTED_CURRENCIES = ("UGX", "KES", "TZS", "RWF", "USD")
DEFAULT_CURRENCY = "UGX"

# Languages the owner can pick (ISO 639-3) — all supported by Sunbird AI.
SUPPORTED_LANGUAGES = ("eng", "lug", "swa", "nyn", "cgg", "xog", "ach", "teo", "lgg", "laj", "kin")
DEFAULT_LANGUAGE = "lug"
REPLY_LANGUAGES = ("english", "same")
DEFAULT_REPLY_LANGUAGE = "english"

_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict]] = {}


def invalidate(org_id: str) -> None:
    _cache.pop(org_id, None)


def _clean(data: Optional[dict]) -> dict:
    data = data or {}
    currency = data.get("currency")
    language = data.get("language")
    reply = data.get("reply_language")
    return {
        "currency": currency if currency in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY,
        "language": language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE,
        "reply_language": reply if reply in REPLY_LANGUAGES else DEFAULT_REPLY_LANGUAGE,
    }


def _remember(org_id: str, data: Optional[dict]) -> dict:
    settings = _clean(data)
    _cache[org_id] = (time.monotonic(), settings)
    return settings


def _cached(org_id: str) -> Optional[dict]:
    hit = _cache.get(org_id)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return None


async def business_settings(profile_repo, org_id: str) -> dict:
    """{"currency", "language", "reply_language"} for this business (never raises)."""
    cached = _cached(org_id)
    if cached:
        return cached
    if profile_repo is None:
        return _clean(None)
    try:
        profile = await profile_repo.get_profile(org_id)
    except Exception:
        profile = None
    if profile is None:
        return _remember(org_id, None)
    return _remember(org_id, {
        "currency": getattr(profile, "currency", None),
        "language": getattr(profile, "language", None),
        "reply_language": getattr(profile, "reply_language", None),
    })


async def business_currency(profile_repo, org_id: str) -> str:
    """Default currency for this business (never raises)."""
    return (await business_settings(profile_repo, org_id))["currency"]


def business_currency_sync(db, org_id: str) -> str:
    """Same, for synchronous callers (briefing, reminders)."""
    cached = _cached(org_id)
    if cached:
        return cached["currency"]
    try:
        doc = (
            db.collection("organizations").document(org_id)
            .collection("profile").document("config").get()
        )
        data = doc.to_dict() if doc.exists else {}
    except Exception:
        data = {}
    return _remember(org_id, data)["currency"]
