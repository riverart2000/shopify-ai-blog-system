"""Promote an event: `promote_event` reads the event pages and plans where to promote it; then one
`promote_post` job per place (listing site, Facebook group, forum) posts it there, fully by itself, in the shared
browser. Joe's own Social Autopilot channels get posts through the Social Engine (no browser needed).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import ModelRetry, RunContext, Tool

from task_agent.guardrails import wrap_untrusted
from task_agent.promotion import (
    DONE_OK,
    MAX_ASKS,
    PACED_KINDS,
    WAIT_FOR_JOE,
    PromotionStore,
    download_image,
    host_of,
    match_channels,
    need_owner,
    normalize,
    pace,
    parse_local,
    post_own_channels_target,
)
from task_agent.tasks.base import Finish, JobDeps, TaskSpec
from task_agent.tasks.research import fetch_page as research_fetch_page
from task_agent.tools import browser as b
from task_agent.tools import web

_URL = re.compile(r"https?://[^\s<>\"']+")
YES = {"yes", "y", "true", "1", "mine", "ours", "our event", "my event"}
NO = {"no", "n", "false", "0", "not mine", "theirs", "someone else's"}


def parse_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    urls = []
    for url in _URL.findall(str(value or "")):
        url = url.rstrip(".,;)]}")
        if url not in urls:
            urls.append(url)
    return urls


# ── shapes ──────────────────────────────────────────────────────────────────


class PromotePayload(BaseModel):
    brief: str = Field(min_length=5, max_length=6_000)  # the filled-in template text
    event_urls: list[str] = Field(min_length=1, max_length=6)
    post_from: str = Field("", max_length=2_000)
    tag: str = Field("", max_length=1_000)
    signup_email: str = Field("", max_length=200)
    my_event: bool = True
    notes: str = Field("", max_length=2_000)
    reuse_from: int | None = None
    max_places: int = Field(15, ge=1, le=30)

    @field_validator("event_urls", mode="before")
    @classmethod
    def _urls(cls, value: Any) -> list[str]:
        urls = parse_urls(value)
        if not urls:
            raise ValueError("add at least one event link (starting with https://)")
        return urls

    @field_validator("my_event", mode="before")
    @classmethod
    def _yes_no(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower().rstrip(".!")
        if not text or text in YES:
            return True
        if text in NO:
            return False
        raise ValueError("answer yes or no")

    @field_validator("signup_email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("that doesn't look like an email address")
        return value

    @field_validator("reuse_from", mode="before")
    @classmethod
    def _reuse(cls, value: Any) -> int | None:
        return int(value) if str(value or "").strip().isdigit() else None


class EventDetails(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    starts_at: str = Field(description="Start in UK time, e.g. 2026-11-15T19:00")
    ends_at: str = Field("", description="End in UK time, if known")
    venue: str = ""
    address: str = ""
    town: str = Field("", description="Town or city, for finding local places")
    online: bool = False
    online_url: str = ""
    price: str = Field("", description="e.g. Free, £10, £5–£15")
    ticket_url: str = Field("", description="The best link for people to book or find out more")
    organiser: str = ""
    description: str = Field("", max_length=3_000, description="What it is, from the event pages, in plain words")
    category: str = Field("", description="e.g. Health & wellness workshop")
    image_url: str = ""


class Place(BaseModel):
    kind: Literal["listing", "group", "forum"]
    name: str = Field(min_length=2, max_length=200)
    url: str = Field(description="The exact web address of the site, group or forum (one you saw)")
    why: str = Field("", max_length=400, description="Why it suits this event")
    account: str = Field(
        "", max_length=200, description="Which of Joe's accounts to use there, e.g. Facebook: joe.bains"
    )


class PromotionPlan(BaseModel):
    event: EventDetails
    places: list[Place] = Field(default_factory=list, max_length=30)
    notes: str = Field("", max_length=1_500, description="Anything Joe should know (missing details, doubts)")


class PlacePayload(BaseModel):
    target_id: int
    promotion_id: int
    kind: Literal["listing", "group", "forum"]
    name: str
    url: str
    why: str = ""
    account: str = ""
    event: EventDetails
    event_urls: list[str] = Field(default_factory=list)
    post_from: str = ""
    tag: str = ""
    signup_email: str = ""
    my_event: bool = True
    notes: str = ""
    image_path: str = ""
    resumed_after_joe: bool = False


class PlaceResult(BaseModel):
    status: Literal["posted", "already_listed", "skipped", "failed"]
    url: str = Field("", description="Address of the live listing or post (open it to check before finishing)")
    note: str = Field("", max_length=600, description="One or two sentences: what you did, or why not")
    login_used: str = Field("", description="Site key of a saved login you used, if any")


# ── shared tools ────────────────────────────────────────────────────────────


async def web_search(ctx: RunContext[JobDeps], query: str) -> str:
    """Search the web. Returns titles, links and snippets. Open the promising ones before relying on them."""
    result = await web.search(query)
    ctx.deps.seen_domains.update(b._domains(result))
    return result


async def fetch_page(ctx: RunContext[JobDeps], url: str) -> str:
    """Read a public web page as plain text (quicker than the browser; no logins, no buttons)."""
    text = await research_fetch_page(ctx, url)
    if not text.startswith("[Blocked") and not text.startswith("[Could not"):
        ctx.deps.seen_domains.add(host_of(url))
    return text


def _seen(deps: JobDeps) -> set[str]:
    seen = set(deps.seen_domains)
    seen.update(host_of(u) for u in deps.opened_urls)
    if deps.browser:
        seen.update(host_of(u) for u in deps.browser.visited(deps.owner))
    return {s for s in seen if s}


def _known(host: str, seen: set[str]) -> bool:
    return any(host == s or host.endswith("." + s) or s.endswith("." + host) for s in seen)


# ── promote_event: plan ─────────────────────────────────────────────────────

PLAN_INSTRUCTIONS = """\
Your job: plan where to promote one event so local people who'd enjoy it find it. You don't post anything:
other workers post to each place you choose, one at a time. Use the browser and the web to look; don't sign up,
join, post or change anything while planning.

1. Work out the event's details from the event pages given to you: title, start and end (UK time, like
   2026-11-15T19:00), venue, address, town, online or not, price, the best link for people to book, organiser,
   a plain description and the picture's address. Use only what the pages say; leave unknown details blank.
2. Find good places to promote it (up to the number asked):
   - kind "listing": free event listing websites and "what's on" pages where an organiser can add an event
     (for example Eventbrite, Meetup, AllEvents, local council, newspaper, radio or venue what's-on pages). Only
     if it's our own event. Leave out the sites in the event links (it's already there).
   - kind "group": Facebook groups for the town or the topic. Start with the groups Joe is in: open
     https://www.facebook.com/groups/joins/ in the browser. Prefer groups whose rules allow events or promotions.
   - kind "forum": community sites where event posts are allowed: Reddit communities for the town or topic,
     Nextdoor, local forums and noticeboards.
   Check each one is real, active and suits this event, and give its exact address. Never invent a place or an
   address: only use ones you saw in a search result or a page.
3. For each place say which of Joe's accounts to use there (from the accounts he listed), if it matters.
Return the plan when you're done.
"""

EXTRACT_JS = """() => {
  const out = {ld: [], meta: {}, title: document.title || ''};
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    out.ld.push((s.textContent || '').slice(0, 20000));
  }
  for (const m of document.querySelectorAll('meta[property], meta[name]')) {
    const key = m.getAttribute('property') || m.getAttribute('name') || '';
    if (/^(og:|twitter:|event:|description$)/.test(key)) {
      out.meta[key] = (m.getAttribute('content') || '').slice(0, 1000);
    }
  }
  out.text = (document.body ? document.body.innerText : '').slice(0, 7000);
  return out;
}"""


def events_in_ld(blocks: list[str]) -> list[dict[str, Any]]:
    """schema.org Event objects in a page's JSON-LD (Eventbrite and Meetup publish these)."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(isinstance(k, str) and k.endswith("Event") for k in kinds):
                found.append(node)
            for key in ("@graph", "mainEntity", "subEvent"):
                if key in node:
                    walk(node[key])

    for block in blocks:
        try:
            walk(json.loads(block))
        except (ValueError, TypeError):
            continue
    return found[:3]


async def read_event_page(deps: JobDeps, url: str) -> str:
    """One event page, as data for the model: structured event data first, then meta tags and the start of the text."""
    data: dict[str, Any] | None = None
    if deps.browser is not None:
        try:
            await web.check_public_url(url)
            page = await deps.browser.page(deps.owner)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(2_500)
            data = await page.evaluate(EXTRACT_JS)
        except Exception as exc:
            data = None
            note = f"[The browser couldn't open it: {type(exc).__name__}]"
        else:
            note = ""
    if data is None:
        try:
            _, text = await web.fetch_text(url, 8_000, client=deps.http)
            deps.opened_urls.add(url)
            return text
        except Exception as exc:
            return wrap_untrusted(url, f"{note} [Couldn't read this page: {type(exc).__name__}]")
    deps.seen_domains.add(host_of(url))
    parts = [f"Page title: {data.get('title', '')}"]
    events = events_in_ld(data.get("ld") or [])
    if events:
        parts.append("Structured event data on the page:\n" + json.dumps(events, ensure_ascii=False)[:6_000])
    if data.get("meta"):
        parts.append("Page tags:\n" + "\n".join(f"{k}: {v}" for k, v in data["meta"].items()))
    parts.append("Page text (start):\n" + str(data.get("text") or ""))
    return wrap_untrusted(url, "\n\n".join(parts))


async def prepare_plan(deps: JobDeps, payload: PromotePayload) -> str:
    sections = []
    for url in payload.event_urls[:4]:
        sections.append(f"Event page {url}:\n{await read_event_page(deps, url)}")
    if deps.store is not None and payload.reuse_from:
        reused = PromotionStore(deps.store).reusable(payload.reuse_from)
        if reused:
            listed = "\n".join(f"- {t['kind']}: {t['name']} {t['url']}" for t in reused)
            sections.append(
                "These places worked for the last event and will be used again automatically, so don't list them:\n"
                + listed
            )
    channels = await _channels(deps)
    if channels:
        names = ", ".join(f"{c['provider']} {c.get('profile') or c.get('name')}" for c in channels)
        sections.append(
            f"Joe's own social accounts ({names}) get posts automatically through Social Autopilot: don't list them."
        )
    return "\n\n".join(sections)


async def _channels(deps: JobDeps) -> list[dict[str, Any]]:
    from task_agent.social import SocialClient, SocialError

    client = SocialClient(deps.settings)
    if not client.configured:
        return []
    try:
        data = await client.get("/channels")
    except SocialError:
        return []
    return [c for c in data.get("channels") or [] if isinstance(c, dict)]


def render_plan_prompt(payload: PromotePayload) -> str:
    accounts = payload.post_from.strip() or "(none listed: the shared browser's logins)"
    return (
        f"Joe's request:\n{payload.brief}\n\n"
        f"Event links:\n" + "\n".join(f"- {u}" for u in payload.event_urls) + "\n"
        f"Joe's accounts to post from:\n{accounts}\n"
        f"Handles to tag: {payload.tag.strip() or '(none)'}\n"
        f"Our own event: {'yes' if payload.my_event else 'no, someone else runs it: only share it, no listings'}\n"
        f"Find up to {payload.max_places} places.\n"
        + (f"Notes from Joe: {payload.notes}\n" if payload.notes.strip() else "")
    )


def check_plan(ctx: RunContext[JobDeps], plan: PromotionPlan) -> PromotionPlan:
    tz = ZoneInfo(ctx.deps.settings.store_timezone)
    if parse_local(plan.event.starts_at, tz) is None:
        raise ModelRetry(f"starts_at '{plan.event.starts_at}' isn't a date and time like 2026-11-15T19:00.")
    seen = _seen(ctx.deps)
    unseen = [p.url for p in plan.places if not _known(host_of(p.url), seen)]
    if unseen:
        raise ModelRetry(
            "These places' addresses never came up in your searches or pages: "
            + ", ".join(unseen)
            + ". Find them with web_search or open them, or leave them out. Never guess an address."
        )
    return plan


def _configure_plan(agent: Any) -> None:
    agent.output_validator(check_plan)


async def plan_finished(fin: Finish) -> None:
    """Turn the plan into places: reused + new, own channels now, listing sites next, groups/forums paced."""
    if fin.status != "done" or fin.output is None:
        return
    store, settings, job = fin.store, fin.settings, fin.job
    ps = PromotionStore(store)
    if ps.for_promotion(job.id):
        return  # already planned (a resumed job)
    plan = PromotionPlan.model_validate(fin.output)
    payload = PromotePayload.model_validate(job.payload)
    tz = ZoneInfo(settings.store_timezone)
    start = now_utc()
    event_start = parse_local(plan.event.starts_at, tz)
    if not (job.template_values or {}).get("_title"):
        store.rename(job.id, f"Promote: {plan.event.title}")
    image_path = await download_image(
        plan.event.image_url, settings.path(settings.db_path).parent / "promotions" / str(job.id)
    )

    places: list[tuple[Place, str]] = []
    seen_urls: set[str] = {normalize(u) for u in payload.event_urls}
    if payload.reuse_from:
        for old in ps.reusable(payload.reuse_from):
            place = Place(kind=old["kind"], name=old["name"], url=old["url"], why=old["why"] or "Worked last time",
                          account=old["account"])  # fmt: skip
            if normalize(place.url) not in seen_urls:
                places.append((place, "reused"))
                seen_urls.add(normalize(place.url))
    for place in plan.places:
        if normalize(place.url) not in seen_urls:
            places.append((place, "found"))
            seen_urls.add(normalize(place.url))

    from task_agent.tasks import TASKS

    spec = TASKS["promote_post"]
    too_late = event_start is not None and event_start <= start
    paced = [(p, s) for p, s in places if p.kind in PACED_KINDS]
    slots = iter(pace(len(paced), start=start, last_paced=ps.last_paced(), event_start=event_start, tz=tz))
    order = 0
    for place, source in places:
        target_id = ps.add(job.id, kind=place.kind, name=place.name, url=place.url, why=place.why,
                           account=place.account, source=source)  # fmt: skip
        if too_late:
            ps.update(target_id, status="skipped", note="The event has already started.")
            continue
        if place.kind == "listing" and not payload.my_event:
            ps.update(target_id, status="skipped", note="Not our event, so it's shared but not listed as ours.")
            continue
        if place.kind in PACED_KINDS:
            when = next(slots)
            if when is None:
                ps.update(target_id, status="skipped", note="Too close to the event to post at a polite pace.")
                continue
        else:
            order += 1
            when = start + timedelta(seconds=order)
        child = PlacePayload(
            target_id=target_id, promotion_id=job.id, kind=place.kind, name=place.name, url=place.url,
            why=place.why, account=place.account, event=plan.event, event_urls=payload.event_urls,
            post_from=payload.post_from, tag=payload.tag, signup_email=payload.signup_email,
            my_event=payload.my_event, notes=payload.notes, image_path=image_path,
        )  # fmt: skip
        child_id, _ = store.enqueue(
            spec.type,
            f"{plan.event.title[:60]} → {place.name[:50]}",
            child.model_dump(mode="json"),
            spec.tool_names,
            priority=1 if place.kind == "listing" else 0,
            run_at=when,
            created_by="promotion",
            parent_id=job.id,
        )
        ps.update(target_id, status="queued", job_id=child_id, scheduled_at=iso_utc(when))

    # Joe's own channels through Social Autopilot.
    if not too_late:
        from task_agent.social import SocialClient, SocialError

        client = SocialClient(settings)
        channels: list[dict[str, Any]] = []
        if client.configured:
            try:
                channels = match_channels((await client.get("/channels")).get("channels") or [], payload.post_from)
            except SocialError as exc:
                log_note = f"Couldn't reach Social Autopilot: {exc}"
                ps.add(job.id, kind="own_social", name="Your channels", status="failed", note=log_note[:300])
        if channels:
            names = ", ".join(f"{c.get('provider')} {c.get('profile') or c.get('name')}" for c in channels)
            target_id = ps.add(job.id, kind="own_social", name=f"Your channels: {names}"[:200],
                               data={"channel_ids": [str(c["channel_id"]) for c in channels]})  # fmt: skip
            await post_own_channels_target(store, settings, store.require(job.id), target_id)


def now_utc() -> datetime:
    from task_agent.db import now

    return now()


def iso_utc(value: datetime) -> str:
    from task_agent.db import iso

    return iso(value)


# ── promote_post: one place ─────────────────────────────────────────────────

POST_INSTRUCTIONS = """\
Your job: promote one event in one place on the web, by yourself, in the shared browser (it's often already
logged in). Work carefully, like a person who wants it done right first time.

1. Open the place. If you need to log in: saved_logins lists Joe's saved logins and type_saved_login fills a
   username or password (you never see passwords). If there's no account and organisers can join free, sign up
   with the sign-up email: call save_new_login first, then use type_saved_login for the password box.
2. If only a person can do the next step (a captcha, a code sent by text or email, a verification email, a
   2-step login, a payment), call ask_joe and say exactly what he must do. Never guess codes.
3. Check it isn't already there (search the site or group for the event's title). If it is, finish with status
   already_listed and its address.
4. kind "listing": create the event as its organiser with exactly the event's details: title, start and end
   (UK time), venue and address (or online), price, booking link, description, category and the event picture
   (upload_event_image). Free listings only.
   kind "group" or "forum": read the rules first (About, rules or sidebar). If they don't allow event or
   promotional posts, finish with status skipped and say why. If Joe isn't a member, ask to join this one group;
   if that needs approval, finish skipped with note "asked to join". Otherwise write a short, friendly post in
   your own words for this audience: what it is, when, where, price and the link, tagging the handles given if
   the site supports it. Write it fresh for this place; don't paste the same text everywhere.
5. Press the final Post / Publish / Submit button yourself. Then open the new listing or post to check it's live
   (or waiting for the site's approval) and finish with status posted and its address.

Never pay, change account or privacy settings, delete anything, message people privately, or post twice in the
same place. If the site is broken or refuses, finish with status failed and say why in the note.
"""


def render_post_prompt(p: PlacePayload) -> str:
    event = p.event
    lines = [
        f"Place: {p.name} ({p.kind}) {p.url}",
        f"Why it was chosen: {p.why}" if p.why else "",
        f"Account to use there: {p.account}" if p.account else "",
        f"Joe's accounts: {' '.join(p.post_from.split()) or '(the shared browser logins)'}",
        f"Handles to tag: {' '.join(p.tag.split()) or '(none)'}",
        f"Email for new sign-ups: {p.signup_email or '(none given: use a saved login, or ask_joe)'}",
        "It's our own event." if p.my_event else "Someone else runs this event: share it, don't list it as ours.",
        f"Event picture: {'ready for upload_event_image' if p.image_path else 'none'}",
        f"Notes from Joe: {p.notes}" if p.notes.strip() else "",
        "Joe has just finished what you asked him to do here. Carry on from the start." if p.resumed_after_joe else "",
        "",
        "The event (from its own pages):",
        wrap_untrusted(
            "event details",
            json.dumps(event.model_dump(exclude={"image_url"}), ensure_ascii=False, indent=1)
            + "\nEvent links: "
            + " ".join(p.event_urls),
        ),
    ]
    return "\n".join(line for line in lines if line is not None and (line or line == ""))


def check_place(ctx: RunContext[JobDeps], result: PlaceResult) -> PlaceResult:
    if result.status in DONE_OK:
        if not result.url.startswith("http"):
            raise ModelRetry("Give the address of the live listing or post (open it first to check it's there).")
        visited = ctx.deps.browser.visited(ctx.deps.owner) if ctx.deps.browser else []
        if visited and normalize(result.url) not in {normalize(u) for u in visited}:
            # Verify after acting: the address must be one the browser really reached, not a guess.
            raise ModelRetry(
                f"You never opened {result.url} in the browser. Open the listing or post to check it's live, "
                "then finish with the address it shows. If it has no page of its own yet (waiting for approval), "
                "give the address of the page that says so."
            )
    return result


def _configure_post(agent: Any) -> None:
    agent.output_validator(check_place)


async def place_starting(store: Any, job: Any, browser: Any) -> str | None:
    ps = PromotionStore(store)
    target = ps.by_job(job.id)
    if target is None:
        return None
    if target["status"] == "skipped":
        return "Skipped by you."
    if target["status"] == "needs_you":  # the day's wait ran out without Done
        ps.update(target["id"], status="skipped", note="Waited a day for you, so it was skipped. Retry it any time.")
        if browser is not None:
            await browser.release(f"login:{need_owner(target['id'])}")
        return "Waited a day for Joe; skipped."
    ps.update(target["id"], status="working")
    return None


async def place_finished(fin: Finish) -> None:
    ps = PromotionStore(fin.store)
    target = ps.by_job(fin.job.id)
    if target is None:
        return
    if fin.status == "done" and fin.output is not None:
        result = PlaceResult.model_validate(fin.output)
        ps.update(target["id"], status=result.status, result_url=result.url or None, note=result.note or None)
        if result.login_used and result.status in DONE_OK:
            from task_agent.accounts import AccountStore

            AccountStore(fin.store, fin.settings.path(fin.settings.profiles_dir)).mark_login_saved(result.login_used)
    elif fin.status == "failed":
        ps.update(target["id"], status="failed", note=(fin.error or "Failed")[:500])
    elif fin.status == "cancelled":
        if target["status"] != "skipped":
            ps.update(target["id"], status="skipped", note="Cancelled.")
    elif fin.status == "queued":
        ps.update(target["id"], status="queued")
    elif fin.status == "needs_you" and fin.need is not None:
        asks = int(target["asks"] or 0) + 1
        if asks > MAX_ASKS:
            fin.store.cancel(fin.job.id)
            ps.update(target["id"], status="skipped", asks=asks,
                      note=f"Needed you more than {MAX_ASKS} times: {fin.need.reason}"[:500])  # fmt: skip
            return
        ps.update(
            target["id"], status="needs_you", asks=asks, need_site=fin.need.site, need_url=fin.need.url,
            need_reason=fin.need.reason, need_until=iso_utc(now_utc() + WAIT_FOR_JOE),
        )  # fmt: skip
        # Mark the payload so the restarted job knows Joe has been.
        job = fin.store.require(fin.job.id)
        payload = {**job.payload, "resumed_after_joe": True}
        with fin.store._connect() as db:
            db.execute("UPDATE agent_jobs SET payload = ? WHERE id = ?", (json.dumps(payload), job.id))


PROMOTE_EVENT = TaskSpec(
    type="promote_event",
    summary="Promote an event: find listing sites, groups and forums, then post it everywhere suitable",
    instructions=PLAN_INSTRUCTIONS,
    payload_model=PromotePayload,
    output_type=PromotionPlan,
    tools=(
        Tool(web_search, takes_ctx=True),
        Tool(fetch_page, takes_ctx=True),
        *b.tools((b.open_page, b.look, b.find_on_page, b.click, b.scroll, b.go_back, b.wait_for_page)),
    ),
    render_prompt=render_plan_prompt,
    configure=_configure_plan,
    prompt_field="brief",
    value_fields=("event_urls", "post_from", "tag", "signup_email", "my_event", "notes", "reuse_from"),
    sample_values={
        "event_urls": "https://www.eventbrite.co.uk/e/example",
        "signup_email": "events@example.com",
        "my_event": "yes",
    },  # fmt: skip
    multiline_fields=("event_urls", "post_from", "tag", "notes"),
    prepare=prepare_plan,
    on_finish=plan_finished,
    history_processors=(b.keep_recent_pages,),
    max_steps=60,
    max_minutes=45,
    uses_browser=True,
)

PROMOTE_POST = TaskSpec(
    type="promote_post",
    summary="Post an event in one place (made by a promotion)",
    instructions=POST_INSTRUCTIONS,
    payload_model=PlacePayload,
    output_type=PlaceResult,
    tools=(
        *b.tools(b.READ_TOOLS, b.ACT_TOOLS, b.LOGIN_TOOLS),
        Tool(web_search, takes_ctx=True),
    ),
    render_prompt=render_post_prompt,
    configure=_configure_post,
    prompt_field="name",
    on_start=place_starting,
    on_finish=place_finished,
    history_processors=(b.keep_recent_pages,),
    max_steps=80,
    max_minutes=40,
    uses_browser=True,
)
