"""Posting-time planner (Outfy SmartQ semantics).

Given the posts already scheduled per channel, find a time that:
* falls on an allowed weekday inside the posting window (store timezone),
* keeps every channel under its daily cap (overflow rolls to the next day),
* keeps a minimum gap from that channel's other posts,
* prefers each network's best times, otherwise a random minute in the window.

A single time is chosen for all channels of a post so they can be created in
one Postiz request (one request = one `date`).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..models import profile_for
from ..settings import ScheduleSettings

_GRANULARITY_MINUTES = 5
_MAX_LOOKAHEAD_DAYS = 21


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":", 1)
    return time(int(hours), int(minutes))


@dataclass
class ScheduleWindow:
    """Per-campaign overrides of the store-wide schedule settings."""
    days: list[int] | None = None
    window_start: str | None = None
    window_end: str | None = None
    randomize: bool | None = None
    use_best_times: bool | None = None
    fixed_time: str | None = None     # e.g. product of the day at "18:00"


@dataclass
class _ChannelState:
    channel_id: str
    provider: str
    daily_cap: int
    min_gap_seconds: int
    times: list[int] = field(default_factory=list)


class SlotPlanner:
    def __init__(
        self,
        schedule: ScheduleSettings,
        channels: list[dict],
        existing_targets: list[dict],
        *,
        now: datetime | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.schedule = schedule
        self.tz = ZoneInfo(schedule.timezone)
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.rng = rng or random.Random()
        self.channels: dict[str, _ChannelState] = {}
        for channel in channels:
            cadence = channel.get("cadence") or {}
            provider = str(channel.get("provider") or "")
            cap = cadence.get("max_per_day")
            if cap is None:
                # Store-wide daily volume, never above the network's own limit.
                cap = min(schedule.posts_per_channel_per_day, schedule.daily_caps.get(provider, 100))
            gap = cadence.get("min_gap_minutes")
            if gap is None:
                gap = schedule.min_gap_minutes
            self.channels[str(channel["channel_id"])] = _ChannelState(
                channel_id=str(channel["channel_id"]), provider=provider,
                daily_cap=max(0, int(cap)), min_gap_seconds=max(0, int(gap)) * 60,
            )
        for target in existing_targets:
            state = self.channels.get(str(target.get("channel_id")))
            if state is not None and target.get("scheduled_at"):
                state.times.append(int(target["scheduled_at"]))

    # ------------------------------------------------------------- helpers

    def local_today(self) -> date:
        return self.now.astimezone(self.tz).date()

    def local_datetime(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, tzinfo=self.tz)

    def _count_on_day(self, state: _ChannelState, day: date) -> int:
        start = int(self.local_datetime(day, time(0, 0)).timestamp())
        end = int(self.local_datetime(day + timedelta(days=1), time(0, 0)).timestamp())
        return sum(1 for ts in state.times if start <= ts < end)

    def _channel_free(self, state: _ChannelState, when: datetime) -> bool:
        ts = int(when.timestamp())
        if self._count_on_day(state, when.astimezone(self.tz).date()) >= state.daily_cap:
            return False
        return all(abs(ts - other) >= state.min_gap_seconds for other in state.times)

    def remaining_today(self, channel_id: str, day: date) -> int:
        state = self.channels.get(channel_id)
        if state is None:
            return 0
        return max(0, state.daily_cap - self._count_on_day(state, day))

    def _candidates(self, day: date, window: ScheduleWindow, channel_ids: list[str]) -> list[datetime]:
        if window.fixed_time:
            return [self.local_datetime(day, _parse_hhmm(window.fixed_time))]
        start = _parse_hhmm(window.window_start or self.schedule.window_start)
        end = _parse_hhmm(window.window_end or self.schedule.window_end)
        start_dt = self.local_datetime(day, start)
        end_dt = self.local_datetime(day, end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)  # window crosses midnight
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        grid = [start_dt + timedelta(minutes=m) for m in range(0, max(1, minutes), _GRANULARITY_MINUTES)]

        preferred: list[datetime] = []
        use_best = self.schedule.use_best_times if window.use_best_times is None else window.use_best_times
        if use_best:
            for channel_id in channel_ids:
                state = self.channels.get(channel_id)
                for hhmm in profile_for(state.provider if state else "").default_times:
                    base = self.local_datetime(day, _parse_hhmm(hhmm))
                    jitter = timedelta(minutes=self.rng.randrange(-15, 16, _GRANULARITY_MINUTES))
                    candidate = base + jitter
                    if start_dt <= candidate < end_dt:
                        preferred.append(candidate)
            self.rng.shuffle(preferred)

        randomize = self.schedule.randomize_times if window.randomize is None else window.randomize
        rest = list(grid)
        if randomize:
            self.rng.shuffle(rest)
        return preferred + rest

    # --------------------------------------------------------------- public

    def next_slot(
        self,
        channel_ids: list[str],
        *,
        earliest: datetime | None = None,
        latest: datetime | None = None,
        day: date | None = None,
        window: ScheduleWindow | None = None,
    ) -> datetime | None:
        """Return a UTC time when every channel in `channel_ids` can post, or None.

        With `day` set only that local day is considered (used by daily rules);
        otherwise days are searched from `earliest` up to `latest`/21 days.
        """
        window = window or ScheduleWindow()
        channel_ids = [cid for cid in channel_ids if cid in self.channels]
        if not channel_ids:
            return None
        lead = self.now + timedelta(minutes=10)
        earliest = max(earliest or lead, lead)
        allowed_days = set(window.days if window.days is not None else self.schedule.days)
        first_day = day or earliest.astimezone(self.tz).date()
        last_day = day or (latest.astimezone(self.tz).date() if latest else first_day + timedelta(days=_MAX_LOOKAHEAD_DAYS))
        current = first_day
        while current <= last_day:
            if current.weekday() in allowed_days:
                for candidate in self._candidates(current, window, channel_ids):
                    utc_candidate = candidate.astimezone(timezone.utc)
                    if utc_candidate < earliest or (latest and utc_candidate > latest):
                        continue
                    if all(self._channel_free(self.channels[cid], candidate) for cid in channel_ids):
                        return utc_candidate.replace(second=0, microsecond=0)
            current += timedelta(days=1)
        return None

    def reserve(self, channel_ids: list[str], when: datetime) -> None:
        ts = int(when.timestamp())
        for channel_id in channel_ids:
            state = self.channels.get(channel_id)
            if state is not None:
                state.times.append(ts)

    def unreserve(self, channel_ids: list[str], ts: int) -> None:
        for channel_id in channel_ids:
            state = self.channels.get(channel_id)
            if state is not None and int(ts) in state.times:
                state.times.remove(int(ts))

    def plan(
        self,
        channel_ids: list[str],
        **kwargs,
    ) -> list[tuple[datetime, list[str]]]:
        """Like next_slot, but splits channels into groups when no shared time exists.

        Returns [(utc_time, [channel_ids...]), ...] and reserves the times.
        Channels that cannot be placed at all are omitted.
        """
        remaining = [cid for cid in channel_ids if cid in self.channels]
        groups: list[tuple[datetime, list[str]]] = []
        while remaining:
            when = self.next_slot(remaining, **kwargs)
            if when is not None:
                self.reserve(remaining, when)
                groups.append((when, list(remaining)))
                break
            placed = False
            # Place the most constrained channel alone, then retry the rest together.
            for cid in sorted(remaining, key=lambda c: self.channels[c].daily_cap):
                single = self.next_slot([cid], **kwargs)
                if single is not None:
                    self.reserve([cid], single)
                    groups.append((single, [cid]))
                    remaining.remove(cid)
                    placed = True
                    break
            if not placed:
                break
        return groups
