"""Time helpers.

Timestamps are stored as naive UTC datetimes. Anything that reasons in days
(stage dates, business days, digests) uses the local Israeli calendar date.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")


def utcnow() -> datetime:
    """Current time as naive UTC, which is how SQLite columns store it."""
    return datetime.now(UTC).replace(tzinfo=None)


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def to_local(dt: datetime) -> datetime:
    """Convert a stored naive-UTC datetime to Asia/Jerusalem."""
    return dt.replace(tzinfo=UTC).astimezone(LOCAL_TZ)
