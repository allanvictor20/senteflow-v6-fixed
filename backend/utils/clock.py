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
    """Strip the tzinfo from an aware datetime so it can be compared with utc_now()."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value
