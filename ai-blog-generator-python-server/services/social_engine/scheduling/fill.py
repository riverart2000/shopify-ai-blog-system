"""Keep every day's posting slots used (Outfy: N posts per network per day).

* `daily_fill_status` — per channel, how many of each day's slots are taken.
* `reslot_missed_approvals` — a post still waiting for approval when its time
  passes is moved to the next free slot once; if it misses again it expires
  (so an unattended queue can never block the auto-pilot from filling days).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db.social_engine import config_store
from db.social_engine import posts as post_store

from ..activity import record
from ..settings import load_settings
from .slots import SlotPlanner

CAPACITY_STATUSES = ("pending", "publishing", "uncertain", "draft", "scheduled", "published")
_GRACE_SECONDS = 300


def channel_cap(settings, channel: dict) -> int:
    cadence = channel.get("cadence") or {}
    if cadence.get("max_per_day") is not None:
        return int(cadence["max_per_day"])
    schedule = settings.schedule
    return min(schedule.posts_per_channel_per_day, schedule.daily_caps.get(channel.get("provider", ""), 100))


async def daily_fill_status(store_id: str, *, days: int = 3) -> list[dict[str, Any]]:
    settings = await load_settings(store_id)
    tz = ZoneInfo(settings.schedule.timezone)
    channels = await config_store.list_channels(store_id, usable_only=True)
    today = datetime.now(tz).date()
    start = int(datetime.combine(today, datetime.min.time(), tzinfo=tz).timestamp())
    end = int(datetime.combine(today + timedelta(days=days), datetime.min.time(), tzinfo=tz).timestamp())
    targets = await post_store.list_targets(store_id, statuses=CAPACITY_STATUSES, scheduled_from=start,
                                            scheduled_to=end, limit=5000)
    targets += await post_store.planned_channel_times(store_id, scheduled_from=start, scheduled_to=end)
    counts: dict[tuple[str, str], int] = {}
    for target in targets:
        day = datetime.fromtimestamp(int(target["scheduled_at"]), tz=tz).date().isoformat()
        counts[(day, target["channel_id"])] = counts.get((day, target["channel_id"]), 0) + 1
    report = []
    for offset in range(days):
        day = (today + timedelta(days=offset)).isoformat()
        in_window = offset > 0 or datetime.now(tz).strftime("%H:%M") < settings.schedule.window_end
        rows = []
        for channel in channels:
            target = channel_cap(settings, channel)
            planned = counts.get((day, channel["channel_id"]), 0)
            rows.append({"channel_id": channel["channel_id"], "name": channel["name"],
                         "provider": channel["provider"], "planned": planned, "target": target,
                         "full": planned >= target})
        report.append({"date": day, "window_open": in_window, "channels": rows,
                       "filled": sum(r["planned"] for r in rows), "slots": sum(r["target"] for r in rows)})
    return report


async def reslot_missed_approvals(store_id: str) -> dict[str, int]:
    now = int(time.time())
    settings = await load_settings(store_id)
    waiting = await post_store.list_posts(store_id, statuses=("pending_approval",),
                                          scheduled_to=now - _GRACE_SECONDS, limit=200)
    if not waiting:
        return {"moved": 0, "expired": 0}
    channels = await config_store.list_channels(store_id, usable_only=True)
    waiting_ids = {row["id"] for row in waiting}
    existing = [
        t for t in [
            *await post_store.list_targets(store_id, statuses=CAPACITY_STATUSES,
                                           scheduled_from=now - 86400, scheduled_to=now + 22 * 86400),
            *await post_store.planned_channel_times(store_id, scheduled_from=now - 86400,
                                                    scheduled_to=now + 22 * 86400),
        ]
        if t["post_id"] not in waiting_ids
    ]
    planner = SlotPlanner(settings.schedule, channels, existing)
    moved = expired = 0
    for row in waiting:
        post = await post_store.get_post(store_id, row["id"])
        if not post:
            continue
        options = dict(post.get("options") or {})
        misses = int(options.get("missed_approvals") or 0) + 1
        options["missed_approvals"] = misses
        pending = [t for t in post["targets"] if t["status"] == "pending"]
        if misses > 1 or not pending:
            for target in pending:
                await post_store.update_target(target["id"], status="cancelled",
                                               error_type="Expired", error_message="Not approved in time.")
            await post_store.transition_post(
                store_id, post["id"], from_statuses=("pending_approval",), to_status="cancelled",
                error_type="Expired", error_message="Not approved before its posting time (twice); slot released.",
                options=options,
            )
            await record(store_id, "post", post["id"], "Expired: not approved in time; its slot was released",
                         level="warning", stage="schedule")
            expired += 1
            continue
        groups = planner.plan([t["channel_id"] for t in pending])
        times = {cid: int(when.timestamp()) for when, cids in groups for cid in cids}
        if not times:
            continue
        for target in pending:
            if target["channel_id"] in times:
                await post_store.update_target(target["id"], scheduled_at=times[target["channel_id"]])
        options["channel_times"] = {**(options.get("channel_times") or {}), **{k: v for k, v in times.items()}}
        await post_store.update_post(store_id, post["id"], scheduled_at=min(times.values()), options=options)
        new_time = datetime.fromtimestamp(min(times.values()), tz=timezone.utc).astimezone(
            ZoneInfo(settings.schedule.timezone)).strftime("%a %d %b %H:%M")
        await record(store_id, "post", post["id"], f"Not approved in time — moved to {new_time}",
                     level="warning", stage="schedule")
        moved += 1
    return {"moved": moved, "expired": expired}
