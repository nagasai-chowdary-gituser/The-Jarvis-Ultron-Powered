"""
greet.py — the time-aware greeting, in the owner's time, in ONE place.

Two real complaints fixed here. The brief said "Morning, Boss" at every hour,
including when he messaged at night - a greeting that never reads the clock is
worse than none, because it is confidently wrong. And the spoken greeting was a
fixed string for the same reason.

INDIAN TIME, WITHOUT A TZ DATABASE. India is UTC+5:30 all year (no daylight
saving), so the offset is exact and needs no tzdata - which Windows does not ship,
so zoneinfo("Asia/Kolkata") would otherwise raise there. zoneinfo is tried first
(for a different JARVIS_TZ), and the fixed offset is the robust fallback.

The owner's rule, in his words: a NIGHT message gets "good evening", not "good
night". So evening runs from 17:00 to just before morning - there is no "good
night" bucket, because he asked for evening there.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# India Standard Time = UTC+5:30, fixed. Override for another zone with
# JARVIS_TZ (an IANA name, needs tzdata) or JARVIS_TZ_OFFSET_MIN (minutes).
_OFFSET_MIN = int(os.environ.get("JARVIS_TZ_OFFSET_MIN") or 330)


def now_local() -> datetime:
    """The current time in the owner's zone. Correct on any machine, whatever its
    clock is set to, because the offset is applied to UTC."""
    tz = (os.environ.get("JARVIS_TZ") or "").strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz))
        except Exception:
            pass
    return datetime.now(timezone.utc) + timedelta(minutes=_OFFSET_MIN)


def time_of_day(now: datetime | None = None) -> str:
    """morning / afternoon / evening. Evening covers the night on purpose."""
    h = (now or now_local()).hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    return "evening"          # 17:00-04:59: a night message gets "evening"


def greeting(now: datetime | None = None, name: str = "Boss") -> str:
    """'Good morning, Boss.' / 'Good afternoon, Boss.' / 'Good evening, Boss.'"""
    return f"Good {time_of_day(now)}, {name}."
