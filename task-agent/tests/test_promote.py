"""Event promotion: the template, planning places, pacing, Joe's hand-overs, clone, and own channels.

No real browser, model or internet: a fake browser hub, scripted models and a mocked Social Engine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from pydantic import SecretStr
from pydantic_ai import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from task_agent.api import AppContext, create_app
from task_agent.db import iso, now
from task_agent.promotion import (
    PromotionStore,
    listed_handles,
    match_channels,
    pace,
    target_done,
    target_retry,
    target_skip,
)
from task_agent.tasks import TASKS
from task_agent.tasks.base import Finish
from task_agent.tasks.promote import PromotePayload, plan_finished
from task_agent.templates import TemplateStore
from task_agent.tools import browser as b
from task_agent.tools import web
from task_agent.worker import run_job

UK = ZoneInfo("Europe/London")
SE = "http://social.test/api/social-engine"
EVENT_URL = "https://www.eventbrite.co.uk/e/breathwork-leeds-123"


# ── fakes ───────────────────────────────────────────────────────────────────


class FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False


class FakeBrowser:
    """Records what the worker asks of the shared browser."""

    def __init__(self) -> None:
        self.pages: dict[str, FakePage] = {}
        self.calls: list[tuple] = []
        self.visits: dict[str, list[str]] = {}

    async def page(self, owner: str) -> FakePage:
        return self.pages.setdefault(owner, FakePage("https://www.meetup.com/create"))

    async def hand_over(self, src: str, dst: str, url: str):
        self.calls.append(("hand_over", src, dst, url))
        self.pages[dst] = self.pages.pop(src, FakePage(url))
        return self.pages[dst], True

    async def release(self, owner: str) -> None:
        self.calls.append(("release", owner))
        self.pages.pop(owner, None)

    def visited(self, owner: str) -> list[str]:
        return self.visits.get(owner, [])

    def take_reopened(self, owner: str) -> bool:
        return False

    def has_tab(self, owner: str) -> bool:
        return owner in self.pages

    def status(self) -> dict[str, Any]:
        return {"running": bool(self.pages), "visible": False, "open_tabs": len(self.pages), "login_tabs": [],
                "profile_saved": False, "busy_job_id": None}  # fmt: skip


@pytest.fixture
def social_settings(settings):
    return settings.model_copy(update={"social_api_url": SE, "social_api_key": SecretStr("se-key")})


CHANNELS = [
    {"channel_id": "fb", "provider": "facebook", "name": "Bio Luxe Lab", "profile": "bioluxelab", "enabled": True},
    {"channel_id": "ig", "provider": "instagram-standalone", "name": "Bioluxe Lab", "profile": "bioluxelab2026",
     "enabled": True},
    {"channel_id": "x", "provider": "x", "name": "Bio Luxe Lab", "profile": "Bioluxelab", "enabled": True,
     "disabled": True},
]  # fmt: skip


def plan_output(starts: datetime, places: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event": {
            "title": "Breathwork in Leeds",
            "starts_at": starts.astimezone(UK).strftime("%Y-%m-%dT%H:%M"),
            "town": "Leeds",
            "venue": "Hyde Park Book Club",
            "price": "£12",
            "ticket_url": EVENT_URL,
            "description": "A calm evening of guided breathing.",
        },
        "places": places,
        "notes": "",
    }


def promote_job(
    store, *, my_event: bool = True, post_from: str = "", reuse_from: int | None = None, event_type: str = ""
):
    payload = PromotePayload(
        brief="Promote this event", event_urls=[EVENT_URL], post_from=post_from, tag="@hydeparkbookclub",
        signup_email="events@bioluxelab.com", my_event=my_event, reuse_from=reuse_from, event_type=event_type,
    ).model_dump(mode="json")  # fmt: skip
    job_id, _ = store.enqueue("promote_event", "Promote an event", payload, [], template_key="promote-event")
    return job_id


async def finish_plan(store, settings, job_id, output) -> None:
    store.finish(job_id, output)
    await plan_finished(Finish(store, settings, store.require(job_id), "done", output=output))


# ── template and payload ────────────────────────────────────────────────────


def test_template_takes_the_event_type_links_and_answers(store):
    templates = TemplateStore(store)
    template = templates.get("promote-event")
    fields = {f["name"]: f for f in template.fields}
    assert fields["event_urls"]["multiline"] and fields["event_urls"]["label"].startswith("Event links")
    assert fields["my_event"]["default"] == "yes" and fields["event_type"]["required"]
    assert "post_from" not in fields and "signup_email" not in fields  # the event type gives these now
    title, payload = templates.build_job(
        template,
        {
            "event_type": "Comedy",  # by name or key
            "event_urls": f"{EVENT_URL}\nhttps://www.meetup.com/leeds-breath/events/9/, and again {EVENT_URL}",
            "my_event": "No",
        },
    )
    assert payload["event_urls"] == [EVENT_URL, "https://www.meetup.com/leeds-breath/events/9/"]
    assert payload["my_event"] is False and payload["event_type"] == "comedy"
    assert "Event type: Comedy." in payload["brief"] and title == "Promote an event: Comedy"
    with pytest.raises(ValueError, match="no event type 'cooking'"):
        templates.build_job(template, {"event_type": "cooking", "event_urls": EVENT_URL})


def test_only_promotions_get_the_event_type_list(store):
    templates = TemplateStore(store)
    options = templates.field_options()
    assert [o["label"] for o in options["event_type"]] == ["Biohacking", "Bloomsbury speakers", "Comedy", "Juggling"]
    promote = {f["name"]: f for f in templates.get("promote-event").to_dict(options)["fields"]}
    listings = {f["name"]: f for f in templates.get("find-event-listings").to_dict(options)["fields"]}
    assert promote["event_type"]["options"] and "options" not in listings["event_type"]  # "a talk" stays free text


def test_listed_handles_are_read_from_account_lines():
    assert listed_handles("Facebook: joe.bains\nInstagram: @bioluxelab2026") == {"joe.bains", "bioluxelab2026"}


def test_a_copy_of_the_promote_template_can_be_saved(store):
    saved = TemplateStore(store).save(
        name="Promote a workshop",
        description="",
        task_type="promote_event",
        prompt="Promote {event_urls} from Facebook: joe.bains. Email {signup_email}.",
    )
    assert saved.task_type == "promote_event"
    with pytest.raises(ValueError, match="Unknown task type"):
        TemplateStore(store).save(name="x", description="", task_type="promote_post", prompt="{name}")


def test_bad_links_and_emails_are_refused():
    with pytest.raises(ValueError, match="event link"):
        PromotePayload(brief="Promote it", event_urls="see facebook")
    with pytest.raises(ValueError, match="email"):
        PromotePayload(brief="Promote it", event_urls=EVENT_URL, signup_email="joe at gmail")


def test_own_channels_are_the_ones_listed_or_all():
    assert [c["channel_id"] for c in match_channels(CHANNELS, "")] == ["fb", "ig"]  # disabled X left out
    assert [c["channel_id"] for c in match_channels(CHANNELS, "Instagram: @bioluxelab2026\nFacebook: joe.bains")] == [
        "ig"
    ]


# ── pacing ──────────────────────────────────────────────────────────────────


def test_groups_are_paced_five_a_day_twenty_minutes_apart_in_daytime():
    start = datetime(2026, 11, 2, 20, 30, tzinfo=UK)  # Monday 20:30
    event = datetime(2026, 11, 5, 19, 0, tzinfo=UK)
    slots = pace(12, start=start, last_paced=None, event_start=event, tz=UK)
    times = [s.astimezone(UK) for s in slots if s]
    assert times[0] == datetime(2026, 11, 2, 20, 30, tzinfo=UK)
    by_day: dict[Any, int] = {}
    for t in times:
        assert 9 <= t.hour < 21
        by_day[t.date()] = by_day.get(t.date(), 0) + 1
    assert max(by_day.values()) <= 5
    assert all(b - a >= timedelta(minutes=20) for a, b in zip(times, times[1:], strict=False))
    # 20:30 and 20:50 on Monday evening, then 5 on Tuesday and 5 on Wednesday.
    assert len(times) == 12 and sorted(by_day.values()) == [2, 5, 5]
    tight = pace(5, start=event - timedelta(hours=3), last_paced=None, event_start=event, tz=UK)
    assert tight[3] == event - timedelta(hours=2) and tight[4] is None  # nothing in the last two hours


def test_pacing_continues_after_other_promotions_group_posts():
    start = datetime(2026, 11, 3, 10, 0, tzinfo=UK)
    last = datetime(2026, 11, 3, 11, 0, tzinfo=UK)
    first = pace(1, start=start, last_paced=last, event_start=None, tz=UK)[0]
    assert first == last + timedelta(minutes=20)


# ── planning ────────────────────────────────────────────────────────────────


async def test_plan_makes_places_reuses_last_time_and_adds_the_types_pages(store, social_settings):
    from task_agent.event_types import EventTypeStore

    EventTypeStore(store).save(
        {"facebook_page": "https://www.facebook.com/joebainscomedy", "linkedin_page": "Joe Bains Comedy"}, key="comedy"
    )
    old = promote_job(store)
    old_targets = PromotionStore(store)
    reused = old_targets.add(old, kind="listing", name="AllEvents", url="https://allevents.in/leeds/old")
    old_targets.update(reused, status="posted")
    failed = old_targets.add(old, kind="forum", name="r/Leeds", url="https://www.reddit.com/r/Leeds/")
    old_targets.update(failed, status="failed")

    job_id = promote_job(store, reuse_from=old, event_type="comedy")
    starts = now() + timedelta(days=6)
    output = plan_output(
        starts,
        [
            {"kind": "listing", "name": "Meetup", "url": "https://www.meetup.com/"},
            {"kind": "listing", "name": "AllEvents again", "url": "https://allevents.in/leeds/old/"},  # duplicate
            {"kind": "group", "name": "Leeds Wellbeing", "url": "https://www.facebook.com/groups/leedswell"},
            {"kind": "group", "name": "Yorkshire Yoga", "url": "https://www.facebook.com/groups/yoga"},
            {"kind": "forum", "name": "Nextdoor Headingley", "url": "https://nextdoor.co.uk/neighborhood/x"},
        ],
    )
    with respx.mock(assert_all_called=False) as mock:
        channels = mock.get(f"{SE}/channels").mock(return_value=httpx.Response(200, json={"channels": CHANNELS}))
        posts = mock.post(f"{SE}/posts").mock(return_value=httpx.Response(200, json={"ok": True}))
        await finish_plan(store, social_settings, job_id, output)
    assert channels.call_count == 0 and posts.call_count == 0  # Social Autopilot is only for Bio Luxe Lab

    targets = PromotionStore(store).for_promotion(job_id)
    names = [t["name"] for t in targets]
    assert names[:2] == ["AllEvents", "Meetup"] and "r/Leeds" not in names  # only what worked is reused
    assert sum(1 for t in targets if "allevents" in t["url"]) == 1
    assert store.require(job_id).title == "Promote: Breathwork in Leeds"
    children = store.children(job_id)
    assert len(children) == 7 and all(c.type == "promote_post" and c.parent_id == job_id for c in children)
    assert all(c.payload["event_type"] == "comedy" for c in children)
    pages = [t for t in targets if t["kind"] == "page"]
    assert [p["url"] for p in pages] == [
        "https://www.facebook.com/joebainscomedy",
        "https://www.linkedin.com/search/results/companies/?keywords=Joe+Bains+Comedy",
    ]
    assert all(p["status"] == "queued" and p["source"] == "own" for p in pages)
    assert not any(t["kind"] == "own_social" for t in targets)
    groups = sorted(datetime.fromisoformat(t["scheduled_at"]) for t in targets if t["kind"] in ("group", "forum"))
    assert all(b - a >= timedelta(minutes=20) for a, b in zip(groups, groups[1:], strict=False))
    meetup = next(c for c in children if c.payload["name"] == "Meetup")
    assert meetup.payload["event"]["title"] == "Breathwork in Leeds" and meetup.priority == 1


async def test_someone_elses_event_is_shared_but_not_listed(store, social_settings):
    job_id = promote_job(store, my_event=False, post_from="Facebook: joe.bains")
    output = plan_output(
        now() + timedelta(days=3),
        [
            {"kind": "listing", "name": "Meetup", "url": "https://www.meetup.com/"},
            {"kind": "group", "name": "Leeds Wellbeing", "url": "https://www.facebook.com/groups/leedswell"},
        ],
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{SE}/channels").mock(return_value=httpx.Response(200, json={"channels": CHANNELS}))
        posts = mock.post(f"{SE}/posts").mock(return_value=httpx.Response(200, json={"ok": True}))
        await finish_plan(store, social_settings, job_id, output)
    targets = {t["name"]: t for t in PromotionStore(store).for_promotion(job_id)}
    assert targets["Meetup"]["status"] == "skipped" and targets["Leeds Wellbeing"]["status"] == "queued"
    assert posts.call_count == 0  # Joe listed only his personal Facebook, which isn't a Social Autopilot channel


async def test_planner_must_not_invent_places(store, settings, monkeypatch):
    async def no_check(url: str) -> None:
        return None

    async def fake_search(query: str, max_results: int = 8) -> str:
        return web.wrap_untrusted("search", "1. Leeds events\n   https://www.leedsinspired.co.uk/events\n   what's on")

    monkeypatch.setattr(web, "check_public_url", no_check)
    monkeypatch.setattr(web, "search", fake_search)
    job_id = promote_job(store)
    store.claim_next()
    starts = now() + timedelta(days=5)
    invented = {"kind": "listing", "name": "Made up", "url": "https://totally-invented-listings.example/"}
    real = {"kind": "listing", "name": "Leeds Inspired", "url": "https://www.leedsinspired.co.uk/events"}

    def model(messages, info: AgentInfo) -> ModelResponse:
        searched = any(isinstance(p, ToolReturnPart) and p.tool_name == "web_search" for m in messages for p in m.parts)
        if not searched:
            return ModelResponse(parts=[ToolCallPart("web_search", {"query": "leeds what's on"})])
        retried = any("never came up" in str(getattr(p, "content", "")) for m in messages for p in m.parts)
        places = [real] if retried else [real, invented]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan_output(starts, places))])

    with respx.mock(assert_all_called=False) as mock:
        mock.get(EVENT_URL).mock(return_value=httpx.Response(200, html="<title>Breathwork</title><p>Leeds</p>"))
        status = await run_job(store.require(job_id), store, settings, FunctionModel(model))
    assert status == "done"
    assert [t["name"] for t in PromotionStore(store).for_promotion(job_id)] == ["Leeds Inspired"]


# ── one place: hand-over to Joe, Done / Skip / Retry ────────────────────────


def place_job(store, settings, kind: str = "listing") -> tuple[int, int]:
    promotion = promote_job(store)
    store.finish(promotion, plan_output(now() + timedelta(days=4), []))
    ps = PromotionStore(store)
    target_id = ps.add(promotion, kind=kind, name="Meetup", url="https://www.meetup.com/")
    payload = {
        "target_id": target_id, "promotion_id": promotion, "kind": kind, "name": "Meetup",
        "url": "https://www.meetup.com/", "event": plan_output(now() + timedelta(days=4), [])["event"],
        "event_urls": [EVENT_URL], "signup_email": "events@bioluxelab.com",
    }  # fmt: skip
    job_id, _ = store.enqueue("promote_post", "Breathwork → Meetup", payload, [], parent_id=promotion)
    ps.update(target_id, status="queued", job_id=job_id)
    return job_id, target_id


def asks_joe_then_posts(url: str):
    def model(messages, info: AgentInfo) -> ModelResponse:
        prompt = " ".join(str(getattr(p, "content", "")) for m in messages for p in m.parts)
        if "Joe has just finished" not in prompt:
            return ModelResponse(
                parts=[ToolCallPart("ask_joe", {"site_name": "Meetup", "what_joe_must_do": "Solve the captcha"})]
            )
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"status": "posted", "url": url, "note": "Listed."})]
        )

    return FunctionModel(model)


async def test_a_place_that_needs_joe_waits_then_carries_on_after_done(store, settings):
    job_id, target_id = place_job(store, settings)
    hub = FakeBrowser()
    model = asks_joe_then_posts("https://www.meetup.com/leeds/events/42/")
    status = await run_job(store.claim_next(), store, settings, model, browser=hub)
    assert status == "needs_you"
    job = store.require(job_id)
    assert job.status == "queued" and datetime.fromisoformat(job.run_at) > now() + timedelta(hours=23)
    target = PromotionStore(store).get(target_id)
    assert target["status"] == "needs_you" and target["need_reason"] == "Solve the captcha"
    assert ("hand_over", f"job:{job_id}", f"login:need-{target_id}", "https://www.meetup.com/create") in hub.calls
    assert store.claim_next() is None  # parked, not retried straight away

    _, message = await target_done(store, target_id, hub)
    assert "back in the queue" in message and ("release", f"login:need-{target_id}") in hub.calls
    hub.visits[f"job:{job_id}"] = ["https://www.meetup.com/leeds/events/42"]
    assert await run_job(store.claim_next(), store, settings, model, browser=hub) == "done"
    target = PromotionStore(store).get(target_id)
    assert target["status"] == "posted" and target["result_url"] == "https://www.meetup.com/leeds/events/42/"


async def test_a_claimed_post_must_have_been_opened(store, settings):
    job_id, target_id = place_job(store, settings)
    hub = FakeBrowser()
    hub.visits[f"job:{job_id}"] = ["https://www.meetup.com/create", "https://www.meetup.com/leeds/events/42"]

    def model(messages, info: AgentInfo) -> ModelResponse:
        retried = any("never opened" in str(getattr(p, "content", "")) for m in messages for p in m.parts)
        url = "https://www.meetup.com/leeds/events/42" if retried else "https://www.meetup.com/leeds/events/999"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"status": "posted", "url": url})])

    assert await run_job(store.claim_next(), store, settings, FunctionModel(model), browser=hub) == "done"
    assert PromotionStore(store).get(target_id)["result_url"].endswith("/42")


async def test_a_day_without_done_skips_the_place(store, settings):
    job_id, target_id = place_job(store, settings)
    hub = FakeBrowser()
    await run_job(store.claim_next(), store, settings, asks_joe_then_posts("https://x.example"), browser=hub)
    with store._connect() as db:  # the day's wait runs out
        db.execute("UPDATE agent_jobs SET run_at = ? WHERE id = ?", (iso(now() - timedelta(minutes=1)), job_id))
    assert await run_job(store.claim_next(), store, settings, asks_joe_then_posts("x"), browser=hub) == "cancelled"
    target = PromotionStore(store).get(target_id)
    assert target["status"] == "skipped" and "Waited a day" in target["note"]
    assert store.require(job_id).status == "cancelled"

    _, message = await target_retry(store, target_id)
    assert store.require(job_id).status == "queued" and PromotionStore(store).get(target_id)["status"] == "queued"


async def test_skip_stops_a_queued_place(store, settings):
    job_id, target_id = place_job(store, settings)
    await target_skip(store, target_id)
    assert store.require(job_id).status == "cancelled"
    assert PromotionStore(store).get(target_id)["status"] == "skipped"
    with pytest.raises(ValueError, match="already finished"):
        await target_skip(store, target_id)


# ── browser tools ───────────────────────────────────────────────────────────


def test_page_view_keeps_refs_only_on_things_you_can_use():
    raw = """- generic [ref=e1]:
  - generic [ref=e2]:
    - heading "Create an event" [level=1] [ref=e3]
    - generic [ref=e4]:
      - textbox "Event title" [ref=e5]
      - button "Next" [ref=e6] [cursor=pointer]:
        - generic [ref=e7]: 
      - link "Help" [ref=e8] [cursor=pointer]:
        - /url: https://www.meetup.com/help/
    - img [ref=e9]"""
    view = b.compact_snapshot(raw)
    assert view.splitlines() == [
        '- heading "Create an event" [level=1]',
        '- textbox "Event title" [ref=e5]',
        '- button "Next" [ref=e6]:',
        '- link "Help" [ref=e8]:',
        " - url: https://www.meetup.com/help/",
    ]


def test_payment_fields_are_recognised():
    assert b._is_payment({"auto": "cc-number"}) and b._is_payment({"name": "cardNumber"})
    assert b._is_payment({"placeholder": "CVC"}) and not b._is_payment({"name": "event_title"})


def test_old_page_views_are_trimmed():
    def view(n: int) -> ModelRequest:
        content = (
            f'Clicked e{n}.\n<untrusted_data source="x">Page: P{n}\nAddress: https://site/{n}\n...</untrusted_data>'
        )
        return ModelRequest(parts=[ToolReturnPart("click", content, tool_call_id=str(n))])

    history = b.keep_recent_pages([view(1), view(2)])
    assert all("<untrusted_data" in m.parts[0].content for m in history)  # two full pages: nothing changes yet
    history = b.keep_recent_pages([*history, view(3)])
    contents = [m.parts[0].content for m in history]
    assert "Old page view removed" in contents[0] and "https://site/1" in contents[0]
    assert "Old page view removed" in contents[1] and "<untrusted_data" in contents[2]

    # Between batches the conversation only grows at the end, so the model server can reuse its cached prompt.
    before = [m.parts[0].content for m in history]
    history = b.keep_recent_pages([*history, view(4)])
    assert [m.parts[0].content for m in history][:3] == before and "<untrusted_data" in history[3].parts[0].content


def test_new_passwords_are_strong():
    password = b.new_password()
    assert len(password) >= 18 and any(c.isdigit() for c in password) and any(c.isupper() for c in password)
    assert password != b.new_password()


# ── API: clone, places, hidden child jobs ───────────────────────────────────


@pytest.fixture
async def client(store, settings):
    ctx = AppContext.build(
        store, settings.model_copy(update={"task_agent_api_key": SecretStr("k")}), browser=FakeBrowser()
    )
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent", headers={"x-api-key": "k"}) as c:
        yield c


async def test_clone_reuses_places_and_keeps_values(client, store):
    values = {"event_type": "comedy", "event_urls": EVENT_URL, "my_event": "yes"}
    first = (await client.post("/api/jobs", json={"template_key": "promote-event", "values": values})).json()["job"]
    detail = (await client.get(f"/api/jobs/{first['id']}")).json()["job"]
    assert detail["template_values"] == values and detail["promotion"]["targets"] == []

    clone = await client.post(
        "/api/jobs",
        json={"template_key": "promote-event", "values": {**values, "event_urls": "https://fb.com/events/2"},
              "clone_of": first["id"], "title": "Second workshop"},
    )  # fmt: skip
    job = store.require(clone.json()["job"]["id"])
    assert job.payload["reuse_from"] == first["id"] and job.payload["event_urls"] == ["https://fb.com/events/2"]
    assert job.template_values["_title"] == "Second workshop" and "reuse_from" not in job.template_values


async def test_child_jobs_are_hidden_and_places_have_buttons(client, store, settings):
    job_id, target_id = place_job(store, settings)
    listed = (await client.get("/api/jobs")).json()
    assert job_id not in [j["id"] for j in listed["jobs"]]
    assert job_id in [j["id"] for j in (await client.get("/api/jobs?include_children=1")).json()["jobs"]]

    PromotionStore(store).update(target_id, status="needs_you", need_reason="Enter the code", need_url="https://m")
    promotion = (await client.get(f"/api/jobs/{store.require(job_id).parent_id}")).json()["job"]["promotion"]
    assert promotion["targets"][0]["need"]["reason"] == "Enter the code"
    done = await client.post(f"/api/targets/{target_id}/done")
    assert done.status_code == 200 and done.json()["target"]["status"] == "queued"
    again = await client.post(f"/api/targets/{target_id}/done")
    assert again.status_code == 400 and "isn't waiting" in again.json()["detail"]
    status = (await client.get("/api/browser")).json()
    assert "browser" in status


async def test_browser_open_refuses_local_addresses(client):
    response = await client.post("/api/browser/open", json={"url": "http://127.0.0.1:4000/admin"})
    assert response.status_code == 400


def test_promotion_types_are_registered():
    assert TASKS["promote_event"].uses_browser and TASKS["promote_post"].on_finish is not None
    assert "ask_joe" in TASKS["promote_post"].tool_names and "type_text" not in TASKS["promote_event"].tool_names


def test_iso_helpers_round_trip():
    assert datetime.fromisoformat(iso(datetime(2026, 11, 2, tzinfo=UTC))).tzinfo is not None
