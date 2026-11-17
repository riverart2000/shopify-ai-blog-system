"""Respond to refused posts, and move posts that are about to be refused.

`handle_failed_target` decides what happens to one failed channel version:

1. a rule in rules/platform_problems.json that matches the error message
   answers at once (e.g. YouTube's shared daily allowance ran out → learn from
   it and send the post again after the reset);
2. a message no rule knows is read by the AI model (`ai/diagnose.py`), which
   picks from the same responses; its answer is remembered for that message;
3. anything that can't be fixed by sending the post again is left failed with
   a plain explanation of what the owner should do.

`protect_scheduled` is the look-ahead: posts already waiting in Postiz at a
time their network is now known to refuse are moved before they fail.

Automatic retries are capped (`max_auto_retries`), never made for a post that
may already be live (`never_retry`), and never for a post older than
`retry_max_age_days`.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db.social_engine import config_store, observability
from db.social_engine import posts as post_store

from ..activity import record
from ..context import load_store_context
from ..errors import SocialEngineError, describe
from ..jobs.queue import enqueue
from ..scheduling.allowance import (
    AllowanceWindow, ProblemRule, ProblemRules, load_rules, note_used_up, relax_windows, window_for, windows_for,
)
from ..scheduling.fill import CAPACITY_STATUSES, channel_cap
from ..scheduling.slots import SlotPlanner

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")

RETRIES_KEY = "problem_retries"   # post options: target id → automatic retries already made
NOTED_KEY = "problem_noted"       # post options: problems already explained to the owner (said once)
_DIAGNOSIS_PREFIX = "problem:diagnosis:"
_EXTRAS_PREFIX = "problem:extras:"   # engine state: times of the posts added to a channel's days by a retry
_STEP_SECONDS = 300
_SEARCH_DAYS = 14
_RETRY_RESPONSES = ("wait_for_reset", "retry_later", "retry_next_day")


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _local(ts: int, zone: ZoneInfo) -> str:
    return _utc(ts).astimezone(zone).strftime("%a %d %b %H:%M")


def _hhmm(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":", 1)
    return int(hours), int(minutes)


def _message_key(provider: str, message: str) -> str:
    """The same problem with different ids or numbers in it is still the same problem."""
    text = re.sub(r"\d+", "#", " ".join((message or "").casefold().split()))
    return hashlib.sha1(f"{provider}|{text}".encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Choosing the new time
# ---------------------------------------------------------------------------

def _into_posting_hours(moment: datetime, schedule) -> datetime:
    """Without an allowance window, a retry still goes out inside the store's posting hours."""
    zone = ZoneInfo(schedule.timezone)
    local = moment.astimezone(zone)
    start = local.replace(hour=_hhmm(schedule.window_start)[0], minute=_hhmm(schedule.window_start)[1],
                          second=0, microsecond=0)
    end = local.replace(hour=_hhmm(schedule.window_end)[0], minute=_hhmm(schedule.window_end)[1],
                        second=0, microsecond=0)
    if end <= start:      # posting hours cross midnight: any time is as good as another
        return moment
    if local < start:
        return start.astimezone(timezone.utc)
    if local > end:
        return (start + timedelta(days=1)).astimezone(timezone.utc)
    return moment


async def free_moment(
    store_id: str, channel: dict, start: datetime, *, window: AllowanceWindow | None, rules: ProblemRules,
    settings, skip_target_ids: tuple[str, ...] = (),
) -> int:
    """First time at or after `start` that the channel can take one more post, on top of its normal ones.

    A day takes at most `extra_posts_per_day` of these (and never more than its normal number plus that), and
    the post keeps `retry_spacing_minutes` away from the channel's other posts. With an allowance window, a
    "day" is the allowance's own day (reset to reset) and the time stays inside the window. The chosen time is
    remembered as one of that day's extras.
    """
    schedule = settings.schedule
    zone = ZoneInfo(schedule.timezone)
    start = start.astimezone(timezone.utc)
    moment = window.next_open(start) if window else _into_posting_hours(start, schedule)
    rows = await post_store.list_targets(
        store_id, statuses=CAPACITY_STATUSES, channel_id=str(channel["channel_id"]),
        scheduled_from=int(moment.timestamp()) - 2 * 86400,
        scheduled_to=int(moment.timestamp()) + (_SEARCH_DAYS + 2) * 86400, limit=2000,
    )
    taken = sorted(int(r["scheduled_at"]) for r in rows if r.get("scheduled_at") and r["id"] not in skip_target_ids)
    cap = channel_cap(settings, channel) + rules.extra_posts_per_day
    spacing = rules.retry_spacing_minutes * 60
    first = int(moment.timestamp())
    extras_key = f"{_EXTRAS_PREFIX}{store_id}:{channel['channel_id']}"
    extras = [int(ts) for ts in await observability.get_state(extras_key, []) or [] if int(ts) > first - 2 * 86400]

    async def chosen(ts: int) -> int:
        await observability.set_state(extras_key, sorted(extras + [ts]))
        return ts

    for _ in range(_SEARCH_DAYS):
        if window:
            day_start, day_end = window.last_reset(moment), window.next_reset(moment)
            close = window.closes(moment)
        else:
            local = moment.astimezone(zone)
            day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            close = local.replace(hour=_hhmm(schedule.window_end)[0], minute=_hhmm(schedule.window_end)[1],
                                  second=0, microsecond=0)
            if close <= local:
                close = day_end
        used = sum(1 for ts in taken if day_start.timestamp() <= ts < day_end.timestamp())
        added = sum(1 for ts in extras if day_start.timestamp() <= ts < day_end.timestamp())
        if used < cap and added < rules.extra_posts_per_day:
            ts = int(moment.timestamp())
            while ts <= close.timestamp():
                if all(abs(ts - other) >= spacing for other in taken):
                    return await chosen(ts)
                ts += _STEP_SECONDS
        moment = window.next_open(day_end) if window else _into_posting_hours(day_end, schedule)
    return await chosen(first)


async def _retry_start(
    response: str, *, now: int, wait_minutes: int, window: AllowanceWindow | None, schedule,
) -> datetime:
    if response == "retry_later":
        return _utc(now + max(1, wait_minutes) * 60)
    if response == "retry_next_day":
        zone = ZoneInfo(schedule.timezone)
        tomorrow = _utc(now).astimezone(zone) + timedelta(days=1)
        hours, minutes = _hhmm(schedule.window_start)
        return tomorrow.replace(hour=hours, minute=minutes, second=0, microsecond=0).astimezone(timezone.utc)
    return window.next_open(_utc(now)) if window else _utc(now)   # wait_for_reset


# ---------------------------------------------------------------------------
# The AI's answer for a problem no rule knows
# ---------------------------------------------------------------------------

async def _diagnose(sctx, rules: ProblemRules, target: dict, made: int, *, allow_ai: bool) -> dict | None:
    """{"response", "wait_minutes", "cause", "advice"} from memory or the model; None = not decided yet."""
    key = _DIAGNOSIS_PREFIX + _message_key(target["provider"], target.get("error_message") or "")
    remembered, saved_at = await observability.get_state_with_time(key)
    if isinstance(remembered, dict) and saved_at and time.time() - saved_at < rules.ai.remember_days * 86400:
        return remembered
    if not allow_ai:
        return None
    from ..ai.diagnose import diagnose_failure

    zone = ZoneInfo(sctx.settings.schedule.timezone)
    now = int(time.time())
    recent = await post_store.list_targets(
        sctx.store_id, channel_id=target["channel_id"], scheduled_from=now - 7 * 86400, scheduled_to=now, limit=200,
    )
    history = [
        {"due": _local(int(r["scheduled_at"]), zone), "result": r["status"],
         **({"error": (r.get("error_message") or "")[:160]} if r["status"] == "failed" else {})}
        for r in recent[-8:] if r.get("scheduled_at") and r["id"] != target["id"]
    ]
    diagnosis = await diagnose_failure(
        sctx, provider=target["provider"], message=target.get("error_message") or "",
        error_type=target.get("error_type") or "",
        stage="the network refused it when it was due" if target.get("error_type") == "PlatformRejected"
        else "Postiz refused it when it was sent for scheduling",
        attempts=made, scheduled_local=_local(int(target.get("scheduled_at") or now), zone),
        recent_same_network=history, min_minutes=rules.ai.min_retry_minutes, max_minutes=rules.ai.max_retry_minutes,
        subject_id=target["id"],
    )
    answer = diagnosis.model_dump()
    await observability.set_state(key, answer)
    return answer


# ---------------------------------------------------------------------------
# One failed channel version
# ---------------------------------------------------------------------------

async def _note_once(store_id: str, post: dict, note_key: str, message: str, *, level: str) -> None:
    options = dict((await post_store.get_post(store_id, post["id"], with_children=False) or post).get("options") or {})
    noted = list(options.get(NOTED_KEY) or [])
    if note_key in noted:
        return
    options[NOTED_KEY] = (noted + [note_key])[-20:]
    await post_store.update_post(store_id, post["id"], options=options)
    await record(store_id, "post", post["id"], message, level=level, stage="problem")


async def _requeue(store_id: str, post: dict, target: dict, when: int, *, from_status: str, made: int | None) -> bool:
    moved = await post_store.transition_target(
        target["id"], from_statuses=(from_status,), to_status="pending", scheduled_at=int(when),
        error_type="", error_message="", external_id="", external_group="", release_url="",
    )
    if not moved:
        return False
    fresh = await post_store.get_post(store_id, post["id"], with_children=False) or post
    options = dict(fresh.get("options") or {})
    options["channel_times"] = {**(options.get("channel_times") or {}), target["channel_id"]: int(when)}
    fields: dict[str, Any] = {"options": options, "publish_mode": "schedule"}
    if made is not None:     # a retry after a failure (a protective move is not a retry)
        options[RETRIES_KEY] = {**(options.get(RETRIES_KEY) or {}), target["id"]: made + 1}
        fields.update(status="approved", error_type="", error_message="")
    await post_store.update_post(store_id, post["id"], **fields)
    await enqueue("post.publish", {"post_id": post["id"]}, store_id=store_id, run_in=30,
                  dedupe_key=f"publish-problem:{target['id']}:{int(when)}", correlation_id=post["id"])
    return True


async def handle_failed_target(store_id: str, target_id: str, *, allow_ai: bool = True) -> dict[str, Any]:
    """Decide and carry out the response to one failed channel version. Safe to call again."""
    rules = load_rules()
    target = await post_store.get_target(target_id)
    if not target or target["store_id"] != store_id or target["status"] != "failed":
        return {"response": "skipped", "reason": "not a failed channel version"}
    if (target.get("error_type") or "") in rules.ignore_error_types:
        return {"response": "skipped", "reason": target.get("error_type")}
    post = await post_store.get_post(store_id, target["post_id"])
    if not post or post["status"] in ("cancelled", "rejected"):
        return {"response": "skipped", "reason": "post cancelled"}
    provider = str(target.get("provider") or "")
    message = str(target.get("error_message") or "")
    now = int(time.time())
    made = int(((post.get("options") or {}).get(RETRIES_KEY) or {}).get(target_id) or 0)
    sctx = await load_store_context(store_id, correlation_id=post["id"])
    zone = ZoneInfo(sctx.settings.schedule.timezone)
    note_key = f"{provider}:{_message_key(provider, message)}"

    if rules.blocks_retry(message):
        await _note_once(
            store_id, post, note_key,
            f"{provider} post needs you: the network says it may already be live, so it was not sent again. "
            "Check the account before posting it again.", level="error")
        return {"response": "needs_person", "reason": "may already be live"}

    rule: ProblemRule | None = rules.match(provider, message)
    window: AllowanceWindow | None = await window_for(provider, target["channel_id"])
    learned = ""
    if rule is not None:
        response, wait_minutes, cause, advice = rule.response, int(rule.retry_after_minutes or 0), rule.label, ""
        if rule.response == "wait_for_reset":
            failed_at = int(target.get("last_synced_at") or target.get("scheduled_at") or now)
            before, window = await note_used_up(rule, target["channel_id"], failed_at)
            if window.latest != before.latest:
                learned = (f" {provider} posts now go out {window.describe(zone, _utc(now))} "
                           f"(was until {before.closes(_utc(now)).astimezone(zone):%H:%M}).")
    else:
        if not rules.ai.enabled or not sctx.settings.ai.ai_decisions:
            return {"response": "unhandled", "reason": "no rule matches and AI diagnosis is off"}
        answer = await _diagnose(sctx, rules, target, made, allow_ai=allow_ai)
        if answer is None:
            return {"response": "undecided"}
        response = str(answer.get("response") or "needs_person")
        wait_minutes = int(answer.get("wait_minutes") or 0)
        cause, advice = str(answer.get("cause") or ""), str(answer.get("advice") or "")

    if response not in _RETRY_RESPONSES:
        await _note_once(store_id, post, note_key,
                         f"{provider} post needs you: {cause}{(' ' + advice) if advice else ''}", level="error")
        return {"response": "needs_person", "cause": cause, "advice": advice}

    if now - int(target.get("scheduled_at") or now) > rules.retry_max_age_days * 86400:
        await _note_once(store_id, post, note_key + ":old",
                         f"{provider}: {cause}. The post is more than {rules.retry_max_age_days} days old, "
                         f"so it was not sent again.{learned}", level="warning")
        return {"response": "too_old", "cause": cause}
    if made >= rules.max_auto_retries:
        await _note_once(store_id, post, note_key + ":gave_up",
                         f"{provider} post needs you: {cause}, and it failed again after {made} automatic "
                         f"retr{'y' if made == 1 else 'ies'}.{learned}", level="error")
        return {"response": "needs_person", "cause": cause, "reason": "retries used up"}

    channel = await config_store.get_channel(store_id, target["channel_id"])
    if not channel or not channel.get("enabled") or channel.get("disabled") or channel.get("missing_at"):
        return {"response": "skipped", "reason": "channel is not connected or switched off"}
    start = await _retry_start(response, now=now, wait_minutes=wait_minutes, window=window,
                               schedule=sctx.settings.schedule)
    when = await free_moment(store_id, channel, start, window=window, rules=rules, settings=sctx.settings)
    if not await _requeue(store_id, post, target, when, from_status="failed", made=made):
        return {"response": "skipped", "reason": "changed meanwhile"}
    await record(
        store_id, "post", post["id"],
        f"{provider}: {cause}. Sending it again {_local(when, zone)} (automatic retry {made + 1} of "
        f"{rules.max_auto_retries}).{learned}",
        level="warning", stage="problem",
        data={"rule": rule.key if rule else "ai", "response": response, "retry_at": when},
    )
    return {"response": response, "retry_at": when, "rule": rule.key if rule else "ai", "cause": cause}


async def handle_failures(store_id: str, target_ids: list[str], *, allow_ai: bool = True) -> dict[str, Any]:
    """Handle several failed channel versions; one that can't be handled never stops the rest."""
    counts: dict[str, int] = {}
    undecided: list[str] = []
    failure: BaseException | None = None
    for target_id in target_ids:
        try:
            outcome = await handle_failed_target(store_id, target_id, allow_ai=allow_ai)
        except Exception as exc:   # a refused post is already recorded as failed: never make things worse
            logger.warning("Could not handle failed target %s: %s: %s", target_id, *describe(exc),
                           extra={"store_id": store_id, "correlation_id": target_id})
            outcome = {"response": "error"}
            failure = failure or exc
        response = str(outcome.get("response"))
        counts[response] = counts.get(response, 0) + 1
        if response == "undecided":
            undecided.append(target_id)
    if undecided and not allow_ai:
        await enqueue("post.problems", {"target_ids": undecided}, store_id=store_id,
                      dedupe_key=f"problems:{store_id}:{','.join(sorted(undecided))[:150]}")
    if failure is not None and allow_ai and isinstance(failure, SocialEngineError):
        raise failure   # e.g. the model could not be reached: the job tries again later
    return counts


async def handle_backlog(store_id: str, *, days: int, provider: str = "") -> dict[str, Any]:
    """Run the handler over channel versions that failed in the last `days` days (oldest first)."""
    now = int(time.time())
    failed = await post_store.list_targets(store_id, statuses=("failed",), scheduled_from=now - days * 86400,
                                           scheduled_to=now + 86400, limit=500)
    ids = [t["id"] for t in failed if not provider or t.get("provider") == provider]
    return {"found": len(ids), **await handle_failures(store_id, ids, allow_ai=True)}


# ---------------------------------------------------------------------------
# Look ahead: move what is about to be refused
# ---------------------------------------------------------------------------

async def protect_scheduled(store_id: str) -> dict[str, Any]:
    """Move posts waiting in Postiz at a time their network's allowance is known not to cover."""
    rules = load_rules()
    now = int(time.time())
    lead = rules.leave_alone_minutes_before_posting * 60
    scheduled = await post_store.list_targets(store_id, statuses=("scheduled",), scheduled_from=now + lead,
                                              limit=2000)
    windows = await windows_for(scheduled)
    at_risk = [t for t in scheduled if t.get("external_id") and t["channel_id"] in windows
               and not windows[t["channel_id"]].allows(_utc(t["scheduled_at"]))]
    relaxed = await relax_windows(now)
    if not at_risk:
        return {"moved": 0, "relaxed": relaxed}

    from ..publishers import get_publisher

    sctx = await load_store_context(store_id)
    zone = ZoneInfo(sctx.settings.schedule.timezone)
    channels = await config_store.list_channels(store_id, usable_only=True)
    by_id = {str(c["channel_id"]): c for c in channels}
    risk_ids = tuple(t["id"] for t in at_risk)
    existing = [
        t for t in [
            *await post_store.list_targets(store_id, statuses=CAPACITY_STATUSES, scheduled_from=now - 2 * 86400,
                                           scheduled_to=now + 23 * 86400, limit=5000),
            *await post_store.planned_channel_times(store_id, scheduled_from=now - 2 * 86400,
                                                    scheduled_to=now + 23 * 86400),
        ] if t.get("id") not in risk_ids
    ]
    planner = SlotPlanner(sctx.settings.schedule, channels, existing, windows=await windows_for(channels))
    publisher = get_publisher(sctx, correlation_id=f"protect:{store_id}")
    moved = 0
    try:
        for target in at_risk:
            channel_id = str(target["channel_id"])
            channel = by_id.get(channel_id)
            if channel is None:
                continue
            window = windows[channel_id]
            due = _utc(target["scheduled_at"])
            slot = None
            if not window.used_up_at(due):   # same day, earlier, inside the allowance and the channel's own rules
                slot = planner.next_slot([channel_id], day=due.astimezone(planner.tz).date())
            if slot is not None:
                when = int(slot.timestamp())
            else:
                when = await free_moment(store_id, channel, _utc(max(int(target["scheduled_at"]), now + lead)),
                                         window=window, rules=rules, settings=sctx.settings,
                                         skip_target_ids=risk_ids)
            post = await post_store.get_post(store_id, target["post_id"], with_children=False)
            if not post:
                continue
            try:
                await publisher.delete_post(target["external_id"])
            except SocialEngineError as exc:
                await record(store_id, "post", target["post_id"],
                             f"Could not move the {target['provider']} post out of a risky time: {exc.message}",
                             level="warning", stage="problem")
                continue
            planner.reserve([channel_id], _utc(when))
            if not await _requeue(store_id, post, target, when, from_status="scheduled", made=None):
                continue
            moved += 1
            await record(
                store_id, "post", target["post_id"],
                f"{target['provider']}: moved from {_local(int(target['scheduled_at']), zone)} to "
                f"{_local(when, zone)}. Its daily allowance has been running out before the original time "
                f"(safe hours now {window.describe(zone, _utc(when))}).",
                level="info", stage="problem", data={"rule": window.rule_key, "from": target["scheduled_at"], "to": when},
            )
    finally:
        await publisher.aclose()
    return {"moved": moved, "at_risk": len(at_risk), "relaxed": relaxed}
