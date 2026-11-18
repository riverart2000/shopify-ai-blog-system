"""Platform problems: allowance windows, learning from a used-up allowance, automatic retries, the AI's answer
for unknown problems, and moving scheduled posts out of risky times."""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from db.social_engine import config_store, observability
from db.social_engine import jobs as job_store
from db.social_engine import posts as post_store
from services.social_engine.pipeline import problems
from services.social_engine.pipeline.channels import poll_notifications
from services.social_engine.scheduling import allowance
from services.social_engine.scheduling.slots import ScheduleWindow, SlotPlanner
from services.social_engine.settings import ScheduleSettings, save_settings

PACIFIC = ZoneInfo("America/Los_Angeles")
LONDON = ZoneInfo("Europe/London")
QUOTA = "YouTube's daily upload quota is exhausted for today, please reschedule this post for a later time."

RULES = {
    "max_auto_retries": 2,
    "retry_max_age_days": 7,
    "retry_spacing_minutes": 30,
    "extra_posts_per_day": 1,
    "leave_alone_minutes_before_posting": 20,
    "never_retry": ["may have already published"],
    "ignore_error_types": ["MissingInPostiz"],
    "ai": {"enabled": True, "min_retry_minutes": 15, "max_retry_minutes": 720, "remember_days": 30},
    "fix": {"max_auto_fixes": 2, "resend_after_minutes": 10, "shorten_step_percent": 10, "ai_rewrite": True},
    "rules": [
        {
            "key": "youtube_daily_allowance", "label": "YouTube's daily upload allowance ran out",
            "providers": ["youtube"], "match": ["daily upload quota is exhausted"], "response": "wait_for_reset",
            "allowance": {
                "resets_at": "00:00", "timezone": "America/Los_Angeles",
                "earliest_minutes_after_reset": 15, "latest_minutes_after_reset": 600,
                "floor_minutes_after_reset": 180, "ceiling_minutes_after_reset": 780,
                "safety_margin_minutes": 60, "relax_after_clean_days": 7, "relax_step_minutes": 30,
            },
        },
        {"key": "temporary_platform_error", "label": "The network had a temporary problem", "providers": [],
         "match": ["try again later"], "response": "retry_later", "retry_after_minutes": 60},
        {"key": "text_too_long", "label": "Its text was too long for the network", "providers": [],
         "match": ["post is too long"], "response": "fix_post", "fix": "shorten"},
        {"key": "no_reason_given", "label": "The network refused it without saying why", "providers": [],
         "match": ["no message from postiz", "unknown error"], "response": "retry_later",
         "retry_after_minutes": 60, "max_retries": 1},
    ],
}


@pytest.fixture(autouse=True)
def rules_file(tmp_path, monkeypatch):
    path = tmp_path / "platform_problems.json"
    path.write_text(json.dumps(RULES))
    monkeypatch.setenv(allowance.RULES_FILE_ENV, str(path))
    return path


def _window(latest: int = 600, exhausted_until: int = 0) -> allowance.AllowanceWindow:
    rule = allowance.load_rules().rules[0]
    return allowance._window(rule, {"latest": latest, "exhausted_until": exhausted_until})


def _pacific(hour: int, minute: int = 0, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=PACIFIC)


# ------------------------------------------------------------------ the window

def test_shipped_rules_file_is_valid(monkeypatch):
    monkeypatch.delenv(allowance.RULES_FILE_ENV)
    rules = allowance.load_rules()
    assert rules.match("youtube", QUOTA).response == "wait_for_reset"
    assert rules.match("instagram", QUOTA) is None
    assert rules.match("instagram", "Unknown error, please try again later").response == "retry_later"


def test_window_allows_only_between_earliest_and_latest_after_the_reset():
    window = _window()
    assert not window.allows(_pacific(0, 5))      # before the margin after the reset
    assert window.allows(_pacific(0, 15))
    assert window.allows(_pacific(10, 0))         # 600 minutes after the reset
    assert not window.allows(_pacific(10, 5))
    assert not window.allows(_pacific(23, 0))


def test_next_open_is_now_when_allowed_else_the_next_opening():
    window = _window()
    assert window.next_open(_pacific(3, 0)) == _pacific(3, 0).astimezone(timezone.utc)
    assert window.next_open(_pacific(11, 30)) == _pacific(0, 15, day=17).astimezone(timezone.utc)
    assert window.next_open(_pacific(0, 2)) == _pacific(0, 15).astimezone(timezone.utc)


def test_used_up_allowance_closes_the_rest_of_the_day():
    until = int(_pacific(0, 0, day=17).timestamp())
    window = _window(exhausted_until=until)
    assert not window.allows(_pacific(5, 0))                 # inside the hours, but today's allowance is gone
    assert window.allows(_pacific(5, 0, day=17))
    assert window.next_open(_pacific(5, 0)) == _pacific(0, 15, day=17).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_running_out_pulls_the_window_earlier_and_clean_days_relax_it(se_db):
    rule = allowance.load_rules().rules[0]
    failed_at = int(_pacific(9, 10).timestamp())             # 550 minutes after the reset
    before, after = await allowance.note_used_up(rule, "yt", failed_at)
    assert (before.latest, after.latest) == (600, 490)       # 550 less the 60-minute margin
    assert after.exhausted_until == int(_pacific(0, 0, day=17).timestamp())

    _, later = await allowance.note_used_up(rule, "yt", int(_pacific(11, 0, day=17).timestamp()))
    assert later.latest == 490                                # a later failure never widens the window
    _, floor = await allowance.note_used_up(rule, "yt", int(_pacific(1, 0, day=18).timestamp()))
    assert floor.latest == 180                                # never earlier than the floor

    last = int(_pacific(1, 0, day=18).timestamp())
    assert await allowance.relax_windows(last + 6 * 86400) == []
    changed = await allowance.relax_windows(last + 7 * 86400)
    assert changed[0]["to"] == 210
    assert await allowance.relax_windows(last + 8 * 86400) == []   # one step per clean run
    assert (await allowance.window_for("youtube", "yt")).latest == 210
    assert (await allowance.window_for("youtube", "local:yt")).latest == 600   # another Postiz, another allowance


# ------------------------------------------------------------------ planning

NOW = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)   # Monday 07:00 GMT, an hour before YouTube's reset


def _planner(providers, existing=(), windows=None, **schedule) -> SlotPlanner:
    channels = [{"channel_id": p, "provider": p, "cadence": {}} for p in providers]
    settings = ScheduleSettings(**{"use_best_times": False, **schedule})
    return SlotPlanner(settings, channels, list(existing), now=NOW, rng=random.Random(3),
                       windows=windows or {})


def test_planner_keeps_youtube_inside_its_window():
    window = _window(latest=300)                               # 08:15–13:00 UK
    planner = _planner(["youtube"], windows={"youtube": window}, window_start="08:00", window_end="21:00")
    for _ in range(2):
        when = planner.next_slot(["youtube"])
        planner.reserve(["youtube"], when)
        assert window.allows(when)
        assert when.astimezone(LONDON).hour < 14


def test_other_networks_keep_their_time_when_youtube_cannot_join():
    window = _window(latest=300)
    planner = _planner(["instagram", "youtube"], windows={"youtube": window})
    evening = ScheduleWindow(fixed_time="19:15")
    groups = planner.plan(["instagram", "youtube"], day=NOW.date(), window=evening)
    times = {cid: when for when, cids in groups for cid in cids}
    assert times["instagram"].astimezone(LONDON).strftime("%H:%M") == "19:15"
    assert window.allows(times["youtube"])                     # its own time, same day, inside the allowance
    assert times["youtube"].astimezone(LONDON).date() == NOW.date()


def test_youtube_shares_the_time_when_it_suits():
    planner = _planner(["instagram", "youtube"], windows={"youtube": _window()})
    groups = planner.plan(["instagram", "youtube"], day=NOW.date(), window=ScheduleWindow(fixed_time="10:00"))
    assert len(groups) == 1 and sorted(groups[0][1]) == ["instagram", "youtube"]


# ------------------------------------------------------------- failed posts

async def _channels(store_id: str) -> None:
    for cid, provider in (("yt", "youtube"), ("ig", "instagram"), ("th", "threads"), ("fb", "facebook"), ("x", "x")):
        await config_store.upsert_channel_from_publisher(store_id, {
            "channel_id": cid, "provider": provider, "name": provider.title(),
        })


async def _failed(store_id: str, *, provider: str = "youtube", channel: str = "yt", message: str = QUOTA,
                  error_type: str = "PlatformRejected", ago: int = 600, caption: str = "Hello") -> tuple[str, str]:
    when = int(time.time()) - ago
    post_id, _ = await post_store.create_post(store_id, {
        "content_type": "single_product", "product_ids": [], "status": "failed", "scheduled_at": when,
        "options": {"channel_ids": [channel], "channel_times": {channel: when}},
    })
    ids = await post_store.replace_targets(store_id, post_id, [
        {"channel_id": channel, "provider": provider, "caption": caption, "scheduled_at": when},
    ])
    await post_store.update_target(ids[0], status="failed", error_type=error_type, error_message=message,
                                   external_id="ext-1", last_synced_at=when)
    return post_id, ids[0]


async def _publish_jobs(post_id: str) -> list[dict]:
    return [j for j in await job_store.list_jobs(job_type="post.publish", limit=50)
            if j.get("correlation_id") == post_id]


@pytest.mark.asyncio
async def test_used_up_allowance_is_learned_and_the_post_goes_out_after_the_reset(se_db):
    await _channels(se_db)
    post_id, target_id = await _failed(se_db)
    outcome = await problems.handle_failed_target(se_db, target_id, allow_ai=False)

    assert outcome["response"] == "wait_for_reset" and outcome["rule"] == "youtube_daily_allowance"
    target = await post_store.get_target(target_id)
    window = await allowance.window_for("youtube", "yt")
    assert target["status"] == "pending" and target["external_id"] == "" and target["error_message"] == ""
    assert target["scheduled_at"] >= window.exhausted_until > time.time()
    assert window.allows(datetime.fromtimestamp(target["scheduled_at"], tz=timezone.utc))
    post = await post_store.get_post(se_db, post_id)
    assert post["status"] == "approved" and post["options"][problems.RETRIES_KEY] == {target_id: 1}
    assert len(await _publish_jobs(post_id)) == 1


@pytest.mark.asyncio
async def test_retries_spread_one_extra_post_a_day(se_db):
    await _channels(se_db)
    await save_settings(se_db, {"schedule": {"posts_per_channel_per_day": 1}})
    days = set()
    for index in range(3):
        _, target_id = await _failed(se_db, ago=600 + index * 60)
        await problems.handle_failed_target(se_db, target_id, allow_ai=False)
        target = await post_store.get_target(target_id)
        window = await allowance.window_for("youtube", "yt")
        days.add(window.last_reset(datetime.fromtimestamp(target["scheduled_at"], tz=timezone.utc)))
    assert len(days) == 3    # one extra post a day: three retries land on three different days


@pytest.mark.asyncio
async def test_retries_are_capped_and_then_a_person_is_asked(se_db):
    await _channels(se_db)
    post_id, target_id = await _failed(se_db)
    for _ in range(2):
        assert (await problems.handle_failed_target(se_db, target_id, allow_ai=False))["response"] == "wait_for_reset"
        await post_store.update_target(target_id, status="failed", error_type="PlatformRejected",
                                       error_message=QUOTA, scheduled_at=int(time.time()) - 60)
    outcome = await problems.handle_failed_target(se_db, target_id, allow_ai=False)
    assert outcome == {"response": "needs_person", "cause": "YouTube's daily upload allowance ran out",
                       "reason": "retries used up"}
    assert (await post_store.get_target(target_id))["status"] == "failed"


@pytest.mark.asyncio
async def test_a_post_that_may_be_live_is_never_sent_again(se_db):
    await _channels(se_db)
    _, target_id = await _failed(
        se_db, provider="instagram", channel="ig",
        message="Pinterest may have already published this pin, please try again later")
    outcome = await problems.handle_failed_target(se_db, target_id)
    assert outcome["response"] == "needs_person"
    assert (await post_store.get_target(target_id))["status"] == "failed"


@pytest.mark.asyncio
async def test_old_posts_and_deleted_posts_are_left_alone(se_db):
    await _channels(se_db)
    _, old = await _failed(se_db, ago=8 * 86400)
    assert (await problems.handle_failed_target(se_db, old, allow_ai=False))["response"] == "too_old"
    assert (await allowance.window_for("youtube", "yt")).latest <= 600    # still learned from
    _, gone = await _failed(se_db, error_type="MissingInPostiz", message="This post is no longer in Postiz")
    assert (await problems.handle_failed_target(se_db, gone))["response"] == "skipped"


@pytest.mark.asyncio
async def test_unknown_problem_goes_to_the_model_once_and_is_remembered(se_db, monkeypatch):
    from services.social_engine.ai import diagnose

    await _channels(se_db)
    asked: list[str] = []

    async def fake(sctx, **kwargs):
        asked.append(kwargs["message"])
        assert kwargs["min_minutes"] == 15 and kwargs["max_minutes"] == 720
        return diagnose.Diagnosis(response="retry_later", wait_minutes=45, cause="Instagram's servers were busy")

    monkeypatch.setattr(diagnose, "diagnose_failure", fake)
    post_id, target_id = await _failed(se_db, provider="instagram", channel="ig",
                                       message="Media container 1789 not ready (code 9007)")
    assert (await problems.handle_failed_target(se_db, target_id, allow_ai=False)) == {"response": "undecided"}
    assert not asked

    before = int(time.time())
    outcome = await problems.handle_failed_target(se_db, target_id, allow_ai=True)
    assert outcome["response"] == "retry_later" and outcome["rule"] == "ai"
    target = await post_store.get_target(target_id)
    assert target["status"] == "pending" and target["scheduled_at"] >= before + 45 * 60

    # The same problem with other numbers in it: answered from memory, even without the model.
    _, again = await _failed(se_db, provider="instagram", channel="ig",
                             message="Media container 2222 not ready (code 9007)")
    assert (await problems.handle_failed_target(se_db, again, allow_ai=False))["response"] == "retry_later"
    assert len(asked) == 1


@pytest.mark.asyncio
async def test_model_saying_needs_person_leaves_the_post_failed_and_says_so_once(se_db, monkeypatch):
    from services.social_engine.ai import diagnose

    await _channels(se_db)

    async def fake(sctx, **kwargs):
        return diagnose.Diagnosis(response="needs_person", cause="The title is longer than YouTube allows",
                                  advice="Shorten the title and retry.")

    monkeypatch.setattr(diagnose, "diagnose_failure", fake)
    post_id, target_id = await _failed(se_db, message="invalidTitle")
    for _ in range(2):
        assert (await problems.handle_failed_target(se_db, target_id))["response"] == "needs_person"
    assert (await post_store.get_target(target_id))["status"] == "failed"
    notes = [a for a in await observability.list_activity(store_id=se_db, entity_id=post_id, limit=50)
             if a["stage"] == "problem"]
    assert len(notes) == 1 and "Shorten the title" in notes[0]["message"]


def test_diagnosis_wait_is_cleaned():
    from services.social_engine.ai.diagnose import Diagnosis

    assert Diagnosis(response="retry_later", wait_minutes="90.0", cause="busy").wait_minutes == 90
    assert Diagnosis(response="retry_later", wait_minutes=None, cause="busy").wait_minutes == 0


# ----------------------------------------------------------------- look ahead

class _FakePublisher:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_post(self, external_id: str) -> None:
        self.deleted.append(external_id)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_scheduled_posts_are_moved_out_of_a_used_up_day(se_db, monkeypatch):
    import services.social_engine.publishers as publishers

    await _channels(se_db)
    fake = _FakePublisher()
    monkeypatch.setattr(publishers, "get_publisher", lambda *a, **k: fake)
    rule = allowance.load_rules().rules[0]
    now = int(time.time())
    _, window = await allowance.note_used_up(rule, "yt", now)          # today's allowance just ran out

    risky = int((window.next_reset(datetime.fromtimestamp(now, tz=timezone.utc)) - timedelta(minutes=5)).timestamp())
    if risky < now + 30 * 60:      # too close to the reset to matter: nothing to move
        pytest.skip("within half an hour of the allowance reset")
    post_id, _ = await post_store.create_post(se_db, {
        "content_type": "single_product", "product_ids": [], "status": "scheduled", "scheduled_at": risky,
        "options": {"channel_ids": ["yt", "ig"], "channel_times": {"yt": risky, "ig": risky}},
    })
    ids = await post_store.replace_targets(se_db, post_id, [
        {"channel_id": "yt", "provider": "youtube", "caption": "Hello", "scheduled_at": risky},
        {"channel_id": "ig", "provider": "instagram", "caption": "Hello", "scheduled_at": risky},
    ])
    for target_id, external in zip(ids, ("ext-yt", "ext-ig")):
        await post_store.update_target(target_id, status="scheduled", external_id=external)

    result = await problems.protect_scheduled(se_db)
    assert result["moved"] == 1 and fake.deleted == ["ext-yt"]
    youtube, instagram = [await post_store.get_target(i) for i in ids]
    assert youtube["status"] == "pending" and youtube["external_id"] == ""
    assert youtube["scheduled_at"] >= window.exhausted_until
    assert (instagram["status"], instagram["scheduled_at"], instagram["external_id"]) == ("scheduled", risky, "ext-ig")
    post = await post_store.get_post(se_db, post_id)
    assert post["status"] == "scheduled" and problems.RETRIES_KEY not in post["options"]   # a move is not a retry
    assert (await problems.protect_scheduled(se_db))["moved"] == 0


# -------------------------------------------------------------- notifications

@pytest.mark.asyncio
async def test_notifications_are_reported_once_however_their_ids_sort(se_db, monkeypatch):
    import services.social_engine.publishers as publishers
    from services.social_engine.publishers.base import PublisherNotification

    class _Notifier(_FakePublisher):
        async def notifications(self, page: int = 0):
            return [PublisherNotification(external_id="0aaa", created_at=None, content="quota", level="error")]

    monkeypatch.setattr(publishers, "get_publisher", lambda *a, **k: _Notifier())
    # 2,000 ids already remembered, all sorting after the new one.
    await observability.set_state(f"notifications:seen:{se_db}", [f"z{i:05d}" for i in range(2000)])
    assert (await poll_notifications(se_db))["new"] == 1
    assert (await poll_notifications(se_db))["new"] == 0


# ------------------------------------------------------------------ fixing the post itself

TOO_LONG = ('postiz: posts.create failed with HTTP 400: {"statusCode":400,"provider":"threads",'
            '"name":"shop","message":"post is too long, please fix it"}')
LINK = "https://shop.example/products/red-light?utm_source=threads&utm_medium=social"
LONG_CAPTION = ("Red light at home, the easy way. " * 14).strip() + f"\n\nShop now: {LINK}\n\n#redlight #wellness #glow"


def test_shortening_cuts_the_wording_and_keeps_the_link():
    from services.social_engine.ai import repair
    from services.social_engine.ai.text import measure_for

    measure = measure_for("threads")
    assert measure(LONG_CAPTION) > 500
    shorter = repair.shorten_caption(LONG_CAPTION, repair.shorter_goal(LONG_CAPTION, 500, measure, 10), measure)
    assert measure(shorter) <= 500 and LINK in shorter and "#redlight" in shorter
    assert shorter.startswith("Red light at home")

    # Inside the limit by our count yet refused: go a step under what was sent.
    fits = "A calm evening routine. Ten minutes is enough. Start tonight." + f"\n\n{LINK}"
    goal = repair.shorter_goal(fits, 500, measure, 10)
    assert goal == measure(fits) * 90 // 100
    again = repair.shorten_caption(fits, goal, measure)
    assert measure(again) <= goal and LINK in again

    # No room for wording and hashtags: the hashtags go, the link stays.
    tight = repair.shorten_caption(LONG_CAPTION, 160, measure)
    assert measure(tight) <= 160 and LINK in tight and "#redlight" not in tight


@pytest.mark.asyncio
async def test_too_long_text_is_shortened_and_the_other_channels_go_again_unchanged(se_db):
    await _channels(se_db)
    post_id, threads_id = await _failed(se_db, provider="threads", channel="th", message=TOO_LONG,
                                        error_type="ExternalServiceError", caption=LONG_CAPTION)
    _, facebook_id = await _failed(se_db, provider="facebook", channel="fb", message=TOO_LONG,
                                   error_type="ExternalServiceError", caption=LONG_CAPTION)

    fixed = await problems.handle_failed_target(se_db, threads_id, allow_ai=False)      # no model needed
    assert fixed["response"] == "fix_post" and fixed["rule"] == "text_too_long" and fixed["changed"] == ["caption"]
    target = await post_store.get_target(threads_id)
    assert target["status"] == "pending" and len(target["caption"].encode()) <= 500 and LINK in target["caption"]
    post = await post_store.get_post(se_db, post_id, with_children=False)
    assert post["status"] == "approved" and post["options"][problems.FIXES_KEY] == {threads_id: 1}
    assert len(await _publish_jobs(post_id)) == 1
    note = [a for a in await observability.list_activity(store_id=se_db, entity_id=post_id, limit=20)
            if a["stage"] == "problem"][0]
    assert "Shortened its text" in note["message"] and "automatic fix 1 of 2" in note["message"]

    other = await problems.handle_failed_target(se_db, facebook_id, allow_ai=False)
    assert other["response"] == "retry_later" and other["rule"] == "innocent"
    facebook = await post_store.get_target(facebook_id)
    assert facebook["status"] == "pending" and facebook["caption"] == LONG_CAPTION


@pytest.mark.asyncio
async def test_fixes_are_capped(se_db):
    await _channels(se_db)
    post_id, target_id = await _failed(se_db, provider="threads", channel="th", message=TOO_LONG, caption=LONG_CAPTION)
    for number in (1, 2):
        assert (await problems.handle_failed_target(se_db, target_id))["response"] == "fix_post"
        await post_store.update_target(target_id, status="failed", error_message=TOO_LONG)
    outcome = await problems.handle_failed_target(se_db, target_id)
    assert outcome == {"response": "needs_person", "cause": "Its text was too long for the network",
                       "reason": "fixes used up"}


@pytest.mark.asyncio
async def test_a_refusal_without_a_reason_is_tried_once_more_only(se_db):
    await _channels(se_db)
    message = "The platform rejected this post (no message from Postiz)."
    post_id, target_id = await _failed(se_db, provider="x", channel="x", message=message)
    before = int(time.time())
    first = await problems.handle_failed_target(se_db, target_id, allow_ai=False)
    assert first["response"] == "retry_later" and first["rule"] == "no_reason_given"
    assert first["retry_at"] >= before + 60 * 60
    await post_store.update_target(target_id, status="failed", error_message=message)
    second = await problems.handle_failed_target(se_db, target_id, allow_ai=False)
    assert second["response"] == "needs_person" and second["reason"] == "retries used up"


@pytest.mark.asyncio
async def test_model_can_have_the_text_rewritten_and_a_bad_rewrite_never_goes_out(se_db, monkeypatch):
    from services.social_engine.ai import diagnose, repair
    from services.social_engine.errors import ValidationError

    await _channels(se_db)
    seen: dict = {}

    async def fake_diagnosis(sctx, **kwargs):
        seen.update(kwargs)
        return diagnose.Diagnosis(response="fix_post", cause="Instagram allows at most 30 hashtags",
                                  fix="Remove hashtags until 30 or fewer are left.")

    async def fake_rewrite(sctx, *, target, instruction, **kwargs):
        assert instruction.startswith("Remove hashtags")
        return repair.RepairDraft(caption="Glow at home.\n\n#redlight", title="")

    monkeypatch.setattr(diagnose, "diagnose_failure", fake_diagnosis)
    monkeypatch.setattr(repair, "rewrite_refused", fake_rewrite)
    caption = "Glow at home.\n\n" + " ".join(f"#tag{i}" for i in range(40))
    post_id, target_id = await _failed(se_db, provider="instagram", channel="ig", caption=caption,
                                       message="Too many hashtags (code 2207051)")
    assert (await problems.handle_failed_target(se_db, target_id, allow_ai=False)) == {"response": "undecided"}
    outcome = await problems.handle_failed_target(se_db, target_id)
    assert outcome["response"] == "fix_post" and outcome["rule"] == "ai"
    assert seen["content"]["caption"] == caption and seen["can_fix"] is True
    target = await post_store.get_target(target_id)
    assert target["status"] == "pending" and target["caption"] == "Glow at home.\n\n#redlight"

    async def refused_rewrite(sctx, **kwargs):
        raise ValidationError("the link https://x.example is missing or was changed")

    monkeypatch.setattr(repair, "rewrite_refused", refused_rewrite)
    _, other_id = await _failed(se_db, provider="instagram", channel="ig", caption=caption,
                                message="Too many hashtags (code 2207052)")
    outcome = await problems.handle_failed_target(se_db, other_id)
    assert outcome["response"] == "needs_person" and "passed the checks" in outcome["reason"]
    other = await post_store.get_target(other_id)
    assert other["status"] == "failed" and other["caption"] == caption


def test_a_rewrite_is_checked_before_it_may_go_out():
    from services.social_engine.ai import repair
    from services.social_engine.ai.text import measure_for
    from services.social_engine.models import profile_for

    def problems_of(new: str, old: str = f"Sleep better tonight.\n\n{LINK}") -> list[str]:
        return repair.repair_problems(
            repair.RepairDraft(caption=new), caption=old, title="", channel_id="ig", limit=120,
            profile=profile_for("instagram"), measure=measure_for("instagram"), banned_terms=["miracle"], health=True,
        )

    assert problems_of(f"Sleep well tonight.\n\n{LINK}") == []
    assert any("nothing was changed" in p for p in problems_of(f"Sleep better tonight.\n\n{LINK}"))
    assert any("missing or was changed" in p for p in problems_of("Sleep well tonight."))
    assert any("limit is 120" in p for p in problems_of("Sleep well tonight. " * 10 + LINK))
    assert any("banned term" in p for p in problems_of(f"A miracle for sleep.\n\n{LINK}"))
    assert any("laim" in p for p in problems_of(f"This cures insomnia.\n\n{LINK}"))


def test_diagnosis_only_fixes_when_offered_and_told_what_to_change():
    from services.social_engine.ai import diagnose

    assert diagnose.Diagnosis(response="fix_post", cause="Too many tags", fix=" Remove  five ").fix == "Remove five"
