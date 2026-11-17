"""Platform problem rules and learned posting allowances.

`rules/platform_problems.json` says how the engine responds when a network or
Postiz refuses a post (see docs/social-engine/platform-problems.md). Every
value comes from that file: nothing here has a built-in default.

An *allowance* is a limit that runs out during the day and comes back at a
known time, e.g. the YouTube upload allowance that every Postiz customer
shares. Nobody can ask how much of it is left, so the engine learns instead:

* posts for that network are only planned between `earliest` and `latest`
  minutes after the reset (`AllowanceWindow`),
* each time the allowance runs out, `latest` moves to a safety margin before
  that moment and the rest of the day is marked as used up,
* after a run of clean days `latest` creeps later again, up to the ceiling.

The learned values live in engine state, one entry per rule and Postiz server
(a self-hosted Postiz has its own allowance).
"""
from __future__ import annotations

import json
import os
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from db.social_engine import observability

from ..errors import ConfigurationError

RULES_FILE = Path(__file__).resolve().parents[3] / "rules" / "platform_problems.json"
RULES_FILE_ENV = "SOCIAL_PROBLEM_RULES_FILE"   # tests point this at their own copy
_STATE_PREFIX = "allowance:"
_MAIN_SERVER = "main"

Response = Literal["wait_for_reset", "retry_later", "retry_next_day", "needs_person"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Allowance(_Strict):
    resets_at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str
    earliest_minutes_after_reset: int = Field(ge=0, le=1439)
    latest_minutes_after_reset: int = Field(ge=1, le=1439)      # where learning starts
    floor_minutes_after_reset: int = Field(ge=1, le=1439)       # learning never goes earlier than this
    ceiling_minutes_after_reset: int = Field(ge=1, le=1439)     # ...or later than this
    safety_margin_minutes: int = Field(ge=0, le=720)
    relax_after_clean_days: int = Field(ge=1, le=365)
    relax_step_minutes: int = Field(ge=1, le=720)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "Allowance":
        if not (self.earliest_minutes_after_reset < self.floor_minutes_after_reset
                <= self.latest_minutes_after_reset <= self.ceiling_minutes_after_reset):
            raise ValueError("An allowance needs earliest < floor <= latest <= ceiling (minutes after the reset).")
        return self


class ProblemRule(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,59}$")
    label: str = Field(min_length=3)
    providers: list[str]                    # empty = every network
    match: list[str] = Field(min_length=1)  # any of these inside the error message (case ignored)
    response: Response
    retry_after_minutes: int | None = Field(None, ge=1, le=10080)
    allowance: Allowance | None = None

    @model_validator(mode="after")
    def _complete(self) -> "ProblemRule":
        if self.response == "wait_for_reset" and self.allowance is None:
            raise ValueError(f"Rule '{self.key}' waits for a reset, so it needs an 'allowance'.")
        if self.response == "retry_later" and self.retry_after_minutes is None:
            raise ValueError(f"Rule '{self.key}' retries later, so it needs 'retry_after_minutes'.")
        return self

    def covers(self, provider: str) -> bool:
        return not self.providers or provider in self.providers

    def matches(self, provider: str, message: str) -> bool:
        text = (message or "").casefold()
        return self.covers(provider) and any(part.casefold() in text for part in self.match)


class AIDiagnosisRules(_Strict):
    enabled: bool
    min_retry_minutes: int = Field(ge=1, le=10080)
    max_retry_minutes: int = Field(ge=1, le=10080)
    remember_days: int = Field(ge=1, le=365)


class ProblemRules(_Strict):
    about: str = ""
    max_auto_retries: int = Field(ge=0, le=10)
    retry_max_age_days: int = Field(ge=1, le=90)
    retry_spacing_minutes: int = Field(ge=1, le=720)
    extra_posts_per_day: int = Field(ge=1, le=10)
    leave_alone_minutes_before_posting: int = Field(ge=1, le=720)
    never_retry: list[str]
    ignore_error_types: list[str]
    ai: AIDiagnosisRules
    rules: list[ProblemRule]

    def match(self, provider: str, message: str) -> ProblemRule | None:
        return next((rule for rule in self.rules if rule.matches(provider, message)), None)

    def blocks_retry(self, message: str) -> bool:
        text = (message or "").casefold()
        return any(part.casefold() in text for part in self.never_retry)

    def allowance_rule(self, provider: str) -> ProblemRule | None:
        return next((r for r in self.rules if r.allowance is not None and provider and provider in r.providers), None)


_CACHE: tuple[str, float, ProblemRules] | None = None


def rules_file() -> Path:
    return Path(os.environ.get(RULES_FILE_ENV) or RULES_FILE).expanduser()


def load_rules() -> ProblemRules:
    """The rules file, re-read whenever it changes on disk."""
    global _CACHE
    path = rules_file()
    try:
        modified = path.stat().st_mtime
    except OSError as exc:
        raise ConfigurationError(f"The platform problem rules file {path} is missing: {exc}") from exc
    if _CACHE and _CACHE[0] == str(path) and _CACHE[1] == modified:
        return _CACHE[2]
    try:
        rules = ProblemRules.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ConfigurationError(f"The platform problem rules file {path} is not valid: {exc}") from exc
    _CACHE = (str(path), modified, rules)
    return rules


# ---------------------------------------------------------------------------
# The window a network may post in
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllowanceWindow:
    rule_key: str
    label: str
    resets_at: time
    zone: ZoneInfo
    earliest: int               # minutes after the reset
    latest: int                 # minutes after the reset (learned)
    exhausted_until: int = 0    # unix time of the reset that brings a used-up allowance back

    def last_reset(self, when: datetime) -> datetime:
        local = when.astimezone(self.zone)
        reset = datetime.combine(local.date(), self.resets_at, tzinfo=self.zone)
        if reset > local:
            reset = datetime.combine(local.date() - timedelta(days=1), self.resets_at, tzinfo=self.zone)
        return reset

    def next_reset(self, when: datetime) -> datetime:
        last = self.last_reset(when)
        return datetime.combine(last.date() + timedelta(days=1), self.resets_at, tzinfo=self.zone)

    def minutes_after_reset(self, when: datetime) -> int:
        return int((when - self.last_reset(when)).total_seconds() // 60)

    def used_up_at(self, when: datetime) -> bool:
        """True when the allowance is known to be gone at that moment."""
        return when.timestamp() < self.exhausted_until

    def allows(self, when: datetime) -> bool:
        if self.used_up_at(when):
            return False
        return self.earliest <= self.minutes_after_reset(when) <= self.latest

    def opens(self, when: datetime) -> datetime:
        """Start of the window in the allowance day that `when` falls in."""
        return self.last_reset(when) + timedelta(minutes=self.earliest)

    def closes(self, when: datetime) -> datetime:
        return self.last_reset(when) + timedelta(minutes=self.latest)

    def next_open(self, after: datetime) -> datetime:
        """The first allowed moment at or after `after` (UTC)."""
        moment = after.astimezone(timezone.utc)
        if self.used_up_at(moment):
            moment = datetime.fromtimestamp(self.exhausted_until, tz=timezone.utc)
        minutes = self.minutes_after_reset(moment)
        if minutes < self.earliest:
            moment = self.opens(moment)
        elif minutes > self.latest:
            moment = self.next_reset(moment) + timedelta(minutes=self.earliest)
        return moment.astimezone(timezone.utc)

    def describe(self, zone: ZoneInfo, on: datetime) -> str:
        """The window on `on`'s allowance day, in the given (store) time zone."""
        return f"{self.opens(on).astimezone(zone):%H:%M}–{self.closes(on).astimezone(zone):%H:%M}"


def server_of(channel_id: str) -> str:
    from ..publishers.combined import server_name

    return server_name(channel_id) or _MAIN_SERVER


def _state_key(rule_key: str, server: str) -> str:
    return f"{_STATE_PREFIX}{rule_key}:{server}"


def _window(rule: ProblemRule, state: dict) -> AllowanceWindow:
    allowance = rule.allowance
    assert allowance is not None
    hours, minutes = allowance.resets_at.split(":")
    latest = int(state.get("latest") or allowance.latest_minutes_after_reset)
    latest = max(allowance.floor_minutes_after_reset, min(latest, allowance.ceiling_minutes_after_reset))
    return AllowanceWindow(
        rule_key=rule.key, label=rule.label, resets_at=time(int(hours), int(minutes)),
        zone=ZoneInfo(allowance.timezone), earliest=allowance.earliest_minutes_after_reset, latest=latest,
        exhausted_until=int(state.get("exhausted_until") or 0),
    )


async def window_for(provider: str, channel_id: str) -> AllowanceWindow | None:
    rule = load_rules().allowance_rule(provider)
    if rule is None:
        return None
    state = await observability.get_state(_state_key(rule.key, server_of(channel_id)), {}) or {}
    return _window(rule, state)


async def windows_for(channels: list[dict]) -> dict[str, AllowanceWindow]:
    """Channel id → its allowance window, for the channels (or targets) that have one."""
    found: dict[str, AllowanceWindow] = {}
    for channel in channels:
        channel_id = str(channel.get("channel_id") or "")
        if not channel_id or channel_id in found:
            continue
        window = await window_for(str(channel.get("provider") or ""), channel_id)
        if window is not None:
            found[channel_id] = window
    return found


async def note_used_up(rule: ProblemRule, channel_id: str, at: int) -> tuple[AllowanceWindow, AllowanceWindow]:
    """Learn from the allowance running out at `at`. Returns (window before, window after)."""
    key = _state_key(rule.key, server_of(channel_id))
    state = dict(await observability.get_state(key, {}) or {})
    before = _window(rule, state)
    moment = datetime.fromtimestamp(int(at), tz=timezone.utc)
    allowance = rule.allowance
    assert allowance is not None
    safe = before.minutes_after_reset(moment) - allowance.safety_margin_minutes
    state["latest"] = max(allowance.floor_minutes_after_reset, min(before.latest, safe))
    state["exhausted_until"] = max(int(state.get("exhausted_until") or 0), int(before.next_reset(moment).timestamp()))
    state["last_used_up_at"] = max(int(state.get("last_used_up_at") or 0), int(at))
    state["times_used_up"] = int(state.get("times_used_up") or 0) + 1
    await observability.set_state(key, state)
    return before, _window(rule, state)


async def relax_windows(now: int | None = None) -> list[dict]:
    """After enough clean days, let each learned window close a little later again."""
    now = int(now or time_module.time())
    changed: list[dict] = []
    for rule in load_rules().rules:
        allowance = rule.allowance
        if allowance is None:
            continue
        for key, entry in (await observability.list_state(f"{_STATE_PREFIX}{rule.key}:")).items():
            state = dict(entry.get("value") or {})
            latest = int(state.get("latest") or allowance.latest_minutes_after_reset)
            quiet_since = max(int(state.get("last_used_up_at") or 0), int(state.get("last_relaxed_at") or 0))
            if latest >= allowance.ceiling_minutes_after_reset or not quiet_since:
                continue
            if now - quiet_since < allowance.relax_after_clean_days * 86400:
                continue
            state["latest"] = min(allowance.ceiling_minutes_after_reset, latest + allowance.relax_step_minutes)
            state["last_relaxed_at"] = now
            await observability.set_state(key, state)
            changed.append({"rule": rule.key, "server": key.rsplit(":", 1)[-1], "from": latest, "to": state["latest"]})
    return changed


async def learned_state() -> list[dict]:
    """Every allowance rule with what has been learned so far (for the CLI and System Health)."""
    out: list[dict] = []
    for rule in load_rules().rules:
        if rule.allowance is None:
            continue
        entries = await observability.list_state(f"{_STATE_PREFIX}{rule.key}:")
        if not entries:
            entries = {_state_key(rule.key, _MAIN_SERVER): {"value": {}}}
        for key, entry in entries.items():
            state = dict(entry.get("value") or {})
            window = _window(rule, state)
            out.append({
                "rule": rule.key, "label": rule.label, "server": key.rsplit(":", 1)[-1],
                "earliest_minutes_after_reset": window.earliest, "latest_minutes_after_reset": window.latest,
                "resets_at": f"{rule.allowance.resets_at} {rule.allowance.timezone}",
                "used_up_until": window.exhausted_until, "times_used_up": int(state.get("times_used_up") or 0),
                "last_used_up_at": int(state.get("last_used_up_at") or 0),
            })
    return out
