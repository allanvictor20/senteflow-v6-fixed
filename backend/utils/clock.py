"""
Time helpers.

The stdlib's naive-UTC constructor is deprecated (and slated for removal)
because it returns a naive datetime that *claims* to be local time. The obvious
replacement, `datetime.now(timezone.utc)`, is timezone-aware — swapping it in
directly would change every isoformat() string we already have in Firestore
(adding "+00:00") and raise TypeError wherever a stored naive timestamp is
compared with a fresh aware one.

`utc_now()` keeps the existing naive-UTC contract without the deprecation, so
stored data and comparisons stay valid. Use `utc_now_aware()` for new code that
genuinely wants an offset-aware value.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, matching stored timestamps."""
    return utc_now().isoformat()


def utc_now_aware() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def to_naive_utc(value: datetime) -> datetime:
    """
    Convert an aware datetime to naive UTC so it can be compared with utc_now().
    Naive values are assumed to already be UTC and are returned unchanged.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# ── Business-local time ───────────────────────────────────────────────────────
# Reminders and the morning briefing are about the owner's day, not UTC.
# BUSINESS_TZ defaults to Africa/Kampala (UTC+3, no daylight saving).

def business_tz():
    import os
    name = os.environ.get("BUSINESS_TZ", "Africa/Kampala")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        # No tz database available (e.g. a slim image without tzdata):
        # East Africa Time is a fixed +03:00.
        from datetime import timedelta
        return timezone(timedelta(hours=3), "EAT")


def local_now() -> datetime:
    """Current time in the business time zone (timezone-aware)."""
    return datetime.now(business_tz())


def local_to_naive_utc(value: datetime) -> datetime:
    """Treat a naive datetime as business-local time and convert it to naive UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=business_tz())
    return value.astimezone(timezone.utc).replace(tzinfo=None)
