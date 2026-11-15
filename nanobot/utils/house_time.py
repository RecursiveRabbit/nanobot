"""Every time our software writes, it writes the house's zone.

Eastern is house law (Evans 2026-10-05, no compromise): *produced by us → Eastern;
consumed by us → converted before consumption; no math.*  A UTC stamp rendered
without its offset reads exactly like a local one, which is how a resident spent
an evening telling the patron to go to bed at eight in the morning.

One function, so there is one answer to "what time is it when we write it down".
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

HOUSE_TZ = ZoneInfo("America/Detroit")


def house_time(value: datetime | None) -> str | None:
    """ISO-8601 in America/Detroit. Naive input is *taken* as house time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=HOUSE_TZ)
    return value.astimezone(HOUSE_TZ).isoformat()
