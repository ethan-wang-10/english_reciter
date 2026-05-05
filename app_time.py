"""Application time helpers.

All user-facing dates and time statistics use China Standard Time because the
product only serves China users. Keep persisted timestamps naive ISO strings for
backward compatibility with existing JSON data and frontend displays.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]


CHINA_TZ_NAME = "Asia/Shanghai"

if ZoneInfo is not None:
    try:
        CHINA_TZ = ZoneInfo(CHINA_TZ_NAME)
    except Exception:  # pragma: no cover - slim images may lack tzdata
        CHINA_TZ = timezone(timedelta(hours=8), name=CHINA_TZ_NAME)
else:  # pragma: no cover - compatibility fallback
    CHINA_TZ = timezone(timedelta(hours=8), name=CHINA_TZ_NAME)

# Prefer China time for legacy code that still relies on localtime/logging.
os.environ.setdefault("TZ", CHINA_TZ_NAME)
try:
    time.tzset()
except (AttributeError, OSError):
    pass


def china_now() -> datetime:
    """Return current China time as a naive datetime for JSON compatibility."""
    return datetime.now(CHINA_TZ).replace(tzinfo=None)


def china_today() -> date:
    """Return today's date in China Standard Time."""
    return china_now().date()


def china_now_iso(*, timespec: str = "seconds") -> str:
    """Return a China-time ISO string without timezone suffix."""
    return china_now().isoformat(timespec=timespec)


def china_date_from_timestamp(ts: float) -> date:
    """Convert a POSIX timestamp to a China-time calendar date."""
    return datetime.fromtimestamp(ts, CHINA_TZ).date()
