"""Scheduling, occasion calendar and product selection (pure logic)."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.social_engine.campaigns.holidays import easter_sunday, occasion_date, upcoming_occasions
from services.social_engine.campaigns.selection import apply_repeat_policy, order_products
from services.social_engine.scheduling.slots import ScheduleWindow, SlotPlanner
from services.social_engine.settings import ScheduleSettings

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)  # Monday 07:00 GMT


def _channels(*providers: str, cap: int | None = None) -> list[dict]:
    return [
        {"channel_id": f"c{i}", "provider": p, "cadence": {"max_per_day": cap} if cap is not None else {}}
        for i, p in enumerate(providers)
    ]


def _planner(channels, existing=(), **schedule) -> SlotPlanner:
    settings = ScheduleSettings(**{"use_best_times": False, **schedule})
    return SlotPlanner(settings, channels, list(existing), now=NOW, rng=random.Random(1))


def test_slot_is_inside_window_and_allowed_day():
    planner = _planner(_channels("instagram", "facebook"), window_start="10:00", window_end="12:00", days=[1])
    when = planner.next_slot(["c0", "c1"])
    local = when.astimezone(LONDON)
    assert local.weekday() == 1  # Tuesday only
    assert 10 <= local.hour < 12
    assert when.second == 0


def test_daily_cap_rolls_over_to_next_day():
    planner = _planner(_channels("x", cap=1), window_start="09:00", window_end="20:00")
    first = planner.next_slot(["c0"])
    planner.reserve(["c0"], first)
    second = planner.next_slot(["c0"])
    assert second.astimezone(LONDON).date() == first.astimezone(LONDON).date() + timedelta(days=1)


def test_min_gap_respected():
    planner = _planner(_channels("instagram"), window_start="09:00", window_end="21:00", min_gap_minutes=180,
                       posts_per_channel_per_day=5)
    times = []
    for _ in range(3):
        when = planner.next_slot(["c0"], day=date(2026, 10, 26))
        planner.reserve(["c0"], when)
        times.append(when)
    gaps = [abs((a - b).total_seconds()) for i, a in enumerate(times) for b in times[i + 1:]]
    assert min(gaps) >= 180 * 60


def test_existing_posts_count_towards_cap():
    day_ts = int(datetime(2026, 10, 26, 12, 0, tzinfo=LONDON).timestamp())
    planner = _planner(_channels("x", cap=1), existing=[{"channel_id": "c0", "scheduled_at": day_ts}])
    assert planner.next_slot(["c0"], day=date(2026, 10, 26)) is None


def test_fixed_time_for_product_of_the_day():
    planner = _planner(_channels("instagram"))
    when = planner.next_slot(["c0"], day=date(2026, 10, 27), window=ScheduleWindow(fixed_time="18:00"))
    assert when.astimezone(LONDON).strftime("%H:%M") == "18:00"


def test_plan_splits_channels_when_no_shared_time():
    existing = [{"channel_id": "c1", "scheduled_at": int(datetime(2026, 10, 26, h, 0, tzinfo=LONDON).timestamp())}
                for h in (9, 12, 15)]
    planner = _planner(_channels("instagram", "x", cap=3), existing=existing)
    groups = planner.plan(["c0", "c1"], day=date(2026, 10, 26))
    placed = {cid for _, cids in groups for cid in cids}
    assert placed == {"c0"}  # x is full that day, instagram still goes out


def test_never_schedules_in_the_past():
    planner = _planner(_channels("instagram"), window_start="00:00", window_end="23:55")
    assert planner.next_slot(["c0"]) >= NOW + timedelta(minutes=10)


def test_occasion_dates():
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert occasion_date("mothers_day_uk", 2026) == date(2026, 3, 15)
    assert occasion_date("black_friday", 2026) == date(2026, 11, 27)
    assert occasion_date("cyber_monday", 2026) == date(2026, 11, 30)
    assert occasion_date("fathers_day_uk", 2026) == date(2026, 6, 21)
    found = upcoming_occasions(date(2026, 12, 20), date(2027, 1, 5), keys=["christmas", "new_year"])
    assert [o.key for _, o in found] == ["christmas", "new_year"]
    custom = upcoming_occasions(date(2026, 5, 1), date(2026, 5, 31), keys=[],
                                custom=[{"key": "anniv", "name": "Brand birthday", "month": 5, "day": 12}])
    assert custom[0][1].name == "Brand birthday"


def _product(pid: str, *, created_days_ago: int = 100, last_posted_days_ago: int | None = None,
             discount: float = 0) -> dict:
    now = NOW.timestamp()
    return {
        "product_id": pid,
        "shopify_created_at": datetime.fromtimestamp(now - created_days_ago * 86400, tz=timezone.utc).isoformat(),
        "last_posted_at": int(now - last_posted_days_ago * 86400) if last_posted_days_ago is not None else None,
        "post_count": 0 if last_posted_days_ago is None else 1,
        "discount_pct": discount,
    }


def test_order_latest_and_on_sale():
    items = [_product("old", created_days_ago=200), _product("new", created_days_ago=2),
             _product("sale", created_days_ago=50, discount=30)]
    rng = random.Random(0)
    assert order_products(items, "latest", rng, now=NOW.timestamp())[0]["product_id"] == "new"
    assert order_products(items, "on_sale_first", rng, now=NOW.timestamp())[0]["product_id"] == "sale"
    least = order_products([_product("a", last_posted_days_ago=1), _product("b")], "least_posted", rng)
    assert least[0]["product_id"] == "b"


def test_repeat_policy_outfy_semantics():
    items = [_product("posted_recent", last_posted_days_ago=2), _product("never"),
             _product("posted_long_ago", last_posted_days_ago=40)]
    no_repeat = apply_repeat_policy(items, allow_reposting=False, repost_after_days=14, now=NOW.timestamp())
    assert [p["product_id"] for p in no_repeat] == ["never"]
    repeat = apply_repeat_policy(items, allow_reposting=True, repost_after_days=14, now=NOW.timestamp())
    assert [p["product_id"] for p in repeat] == ["never", "posted_long_ago"]


def test_default_daily_limit_is_two_posts_per_channel():
    planner = _planner(_channels("instagram", "facebook"))
    day = date(2026, 10, 27)
    placed = []
    for _ in range(3):
        when = planner.next_slot(["c0", "c1"], day=day)
        if when:
            planner.reserve(["c0", "c1"], when)
            placed.append(when)
    assert len(placed) == 2  # Outfy-style: two posts per network per day


def test_network_limit_wins_when_lower_than_store_default():
    planner = _planner(_channels("gmb"), posts_per_channel_per_day=5)
    assert planner.channels["c0"].daily_cap == 2  # Google Business is limited to 2/day
