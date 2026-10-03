"""Dubai time helpers. Everything is stored in UTC; everything the user sees or types is Dubai time."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Dubai")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse(value) -> datetime | None:
    """ISO text (or datetime) → aware UTC datetime. Text without a zone is taken as UTC."""
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def fmt(value, date: bool = True) -> str:
    """'Sat 3 Oct, 9:00 AM' in Dubai time."""
    dt = parse(value)
    if not dt:
        return ""
    d = local(dt)
    clock = f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    return f"{d:%a} {d.day} {d:%b}, {clock}" if date else clock


def from_local_input(value: str) -> datetime:
    """A browser datetime-local value ('2026-10-03T09:00'), meant as Dubai time → UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def to_local_input(value) -> str:
    dt = parse(value)
    return local(dt).strftime("%Y-%m-%dT%H:%M") if dt else ""
