"""
Due-date parsing for promises and reminders.

The extraction prompt asks the model for ISO dates (YYYY-MM-DD) resolved
against today's date, but owners also write "Friday", "tomorrow" or
"12/06/2026", and older records hold whatever the model returned. This turns
any of those into a calendar date in the business time zone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from utils.clock import business_tz, local_now, to_naive_utc

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_due_date(raw: Any, today: Optional[date] = None) -> Optional[date]:
    """Best-effort conversion of a due date to a calendar date. None if unknown."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    today = today or local_now().date()
    s = str(raw).strip().lower()
    for prefix in ("by ", "on ", "next ", "this "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    if s in ("today", "tonight", "now"):
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    if s in ("week", "next week", "in a week"):
        return today + timedelta(days=7)
    if s in ("end of month", "month end", "end of the month"):
        first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return first_next - timedelta(days=1)
    if s in _WEEKDAYS:
        delta = (_WEEKDAYS[s] - today.weekday()) % 7
        return today + timedelta(days=delta)

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("z", "+00:00")).date()
    except ValueError:
        return None


def reminder_due_at(raw_due: Any, *, after_due_date: bool, hour: int = 9) -> Optional[datetime]:
    """
    When a reminder should fire, as naive UTC (matching stored timestamps).

    after_due_date=True  → the morning after the due date ("Brian promised to
                           pay by Friday" → ask on Saturday whether he did).
    after_due_date=False → the morning of the date ("remind me Friday to call
                           Sarah").
    """
    due = parse_due_date(raw_due)
    if due is None:
        return None
    if after_due_date:
        due = due + timedelta(days=1)
    local_dt = datetime.combine(due, time(hour=hour), tzinfo=business_tz())
    return to_naive_utc(local_dt)
