"""Daily posting schedule (Dubai time).

Every minute:
  1. For each posting time whose "prepare" moment has come, a fresh Auto post is made once
     (topic picked by the variety planner) and emailed for approval, or marked automatic.
  2. Posts whose time has come are published (see pipeline.publish_due).
"""

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta

from . import emailer, pipeline
from .store import get_store
from .timeutil import TZ, fmt, iso, local, now_utc, parse

log = logging.getLogger("scheduler")

DEFAULT = {
    "enabled": False,
    "times": ["09:00", "13:00", "19:00"],
    "days": [0, 1, 2, 3, 4, 5, 6],  # Monday = 0
    "approval": "ask",              # ask = email approval first · auto = publish without approval
    "prepare_minutes": 120,         # make the post this long before its time
    "if_no_answer": "wait",         # ask mode, not approved in time: wait | publish
}
PREPARE_CHOICES = (15, 30, 60, 120, 180, 360, 720)
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LATE_CREATE = timedelta(hours=1)  # after this, a posting time that was missed is skipped
LOOK_BACK = timedelta(hours=24)  # skipped times this far back are reported by email (once)
TICK_SECONDS = 60
_lock = asyncio.Lock()


class ScheduleError(ValueError):
    pass


def validate(data: dict) -> dict:
    """Clean and check a schedule sent from the dashboard."""
    out = dict(DEFAULT)
    out["enabled"] = bool(data.get("enabled"))
    times = []
    raw_times = data.get("times") or []
    for t in (raw_times if isinstance(raw_times, list) else [raw_times]):
        t = str(t).strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ScheduleError(f"'{t}' is not a valid time (use HH:MM, e.g. 09:00)")
        times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    times = sorted(set(times))
    if out["enabled"] and not times:
        raise ScheduleError("Add at least one posting time")
    if len(times) > 8:
        raise ScheduleError("At most 8 posting times per day")
    out["times"] = times
    raw_days = data.get("days", DEFAULT["days"])
    days = sorted({int(d) for d in (raw_days if isinstance(raw_days, list) else []) if str(d).isdigit() and 0 <= int(d) <= 6})
    if out["enabled"] and not days:
        raise ScheduleError("Pick at least one day")
    out["days"] = days
    out["approval"] = "auto" if data.get("approval") == "auto" else "ask"
    try:
        prepare = int(data.get("prepare_minutes") or DEFAULT["prepare_minutes"])
    except (TypeError, ValueError):
        raise ScheduleError("Unknown preparation time") from None
    if prepare not in PREPARE_CHOICES:
        raise ScheduleError("Unknown preparation time")
    out["prepare_minutes"] = prepare
    out["if_no_answer"] = "publish" if data.get("if_no_answer") == "publish" else "wait"
    return out


async def get_schedule() -> dict:
    saved = await get_store().get_setting("schedule") or {}
    return {**DEFAULT, **saved}


async def save_schedule(data: dict) -> dict:
    clean = validate(data)
    # times that had already passed when the schedule was saved are never posted late
    clean["saved_at"] = iso(now_utc())
    await get_store().set_setting("schedule", clean)
    return clean


def slots_between(schedule: dict, start: datetime, end: datetime) -> list[datetime]:
    """All posting times (UTC) from start to end, using Dubai dates, times and days."""
    out = []
    day = local(start).date() - timedelta(days=1)
    last = local(end).date() + timedelta(days=1)
    while day <= last:
        if day.weekday() in schedule["days"]:
            for t in schedule["times"]:
                h, m = map(int, t.split(":"))
                slot = datetime.combine(day, time(h, m), tzinfo=TZ)
                slot_utc = parse(slot)
                if start <= slot_utc <= end:
                    out.append(slot_utc)
        day += timedelta(days=1)
    return sorted(out)


def slot_key(slot: datetime) -> str:
    return iso(slot)


async def prepare_due(now: datetime | None = None) -> list[dict]:
    """Make the posts whose prepare time has come. Each posting time gets at most one post, ever."""
    now = now or now_utc()
    schedule = await get_schedule()
    if not schedule["enabled"]:
        return []
    store = get_store()
    made, missed = [], []
    async with _lock:
        done = list(await store.get_setting("schedule_slots_done") or [])
        prepare = timedelta(minutes=schedule["prepare_minutes"])
        saved_at = parse(schedule.get("saved_at"))
        for slot in slots_between(schedule, now - LOOK_BACK, now + prepare):
            key = slot_key(slot)
            if key in done or now < slot - prepare:
                continue
            if saved_at and slot <= saved_at:
                continue  # this time had already passed when the schedule was saved
            done.append(key)
            await store.set_setting("schedule_slots_done", done[-500:])  # mark first: never two posts per time
            if now - slot > LATE_CREATE:
                log.warning("Posting time %s was missed while the server was offline; skipped", key)
                missed.append(slot)
                continue
            try:
                post = await pipeline.create(
                    "", None, scheduled_for=slot, source="schedule",
                    auto_publish=schedule["approval"] == "auto", if_no_answer=schedule["if_no_answer"],
                )
            except Exception:
                log.exception("Could not start the post for %s", fmt(slot))
                continue
            log.info("Prepared post %s for %s", post["id"], fmt(slot))
            made.append(post)
    if missed:
        await emailer.alert_slots_missed(missed)
    return made


async def tick(now: datetime | None = None) -> None:
    await prepare_due(now)
    await pipeline.publish_due(now)


async def loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)


async def overview(days: int = 7) -> list[dict]:
    """Posting times and scheduled posts for the dashboard: today, and the next days."""
    schedule = await get_schedule()
    now = now_utc()
    start = parse(datetime.combine(local(now).date(), time(0, 0), tzinfo=TZ))
    end = start + timedelta(days=days)
    posts = [p for p in await get_store().list_posts(300) if p.get("scheduled_for")]
    by_time: dict[str, list[dict]] = {}
    for p in posts:
        when = parse(p["scheduled_for"])
        from_schedule = (p.get("prefs") or {}).get("source") == "schedule"
        if p["status"] == "published" and p.get("published_at") and not from_schedule:
            when = parse(p["published_at"])  # a single post published early is listed when it really went out
        if start <= when < end:
            by_time.setdefault(slot_key(when), []).append(p)
    rows = []
    slots = slots_between(schedule, start, end - timedelta(seconds=1)) if schedule["enabled"] else []
    for slot in slots:
        key = slot_key(slot)
        found = by_time.pop(key, [])
        live = [p for p in found if p["status"] != "cancelled"] or found
        rows.append({"when": slot, "posts": live, "slot": True,
                     "prepare_at": slot - timedelta(minutes=schedule["prepare_minutes"])})
    for key, found in by_time.items():
        rows.append({"when": parse(key), "posts": found, "slot": False, "prepare_at": None})
    rows.sort(key=lambda r: r["when"])
    days_out: list[dict] = []
    for r in rows:
        d: date = local(r["when"]).date()
        if not days_out or days_out[-1]["date"] != d:
            label = "Today" if d == local(now).date() else ("Tomorrow" if d == local(now).date() + timedelta(days=1)
                                                             else f"{DAY_NAMES[d.weekday()]} {d.day} {d:%b}")
            days_out.append({"date": d, "label": label, "rows": []})
        r["past"] = r["when"] <= now
        days_out[-1]["rows"].append(r)
    return days_out
