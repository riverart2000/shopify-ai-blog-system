"""Event promotions: the places a promotion posts to (`promotion_targets`), their pacing, Joe's Done / Skip /
Retry, and the posts on his own channels (through the Social Engine).

A promotion is a `promote_event` job. It plans the places, then each listing site, group or forum gets its own
`promote_post` job (`parent_id` = the promotion). Groups and forums are paced so the account isn't flagged as spam.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
import structlog

from task_agent.db import JobError, JobStore, iso, now

log = structlog.get_logger()

KINDS = ("listing", "group", "forum", "page", "own_social")
KIND_LABELS = {
    "listing": "Listing site", "group": "Facebook group", "forum": "Forum", "page": "Your Page",
    "own_social": "Your channels",
}  # fmt: skip
TERMINAL = ("posted", "already_listed", "skipped", "failed")
DONE_OK = ("posted", "already_listed")
WAIT_FOR_JOE = timedelta(hours=24)
MAX_ASKS = 2  # a place that needs Joe more often than this is skipped

# "Careful" pacing for groups and forums (Joe's choice): per event per day, gap between any two such posts.
PACED_KINDS = ("group", "forum")
PER_DAY = 5
GAP = timedelta(minutes=20)
DAY_START, DAY_END = 9, 21  # UK hours when group posts go out
STOP_BEFORE_EVENT = timedelta(hours=2)
MAX_IMAGE_BYTES = 8_000_000


def normalize(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    return urlunsplit(("https", host, parts.path.rstrip("/") or "/", parts.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def parse_local(value: str, tz: ZoneInfo) -> datetime | None:
    """'2026-11-15T19:00' (UK time) or a full ISO time → aware datetime."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def pace(
    count: int,
    *,
    start: datetime,
    last_paced: datetime | None,
    event_start: datetime | None,
    tz: ZoneInfo,
    per_day: int = PER_DAY,
    gap: timedelta = GAP,
) -> list[datetime | None]:
    """Times for `count` group/forum posts: daytime only, at most `per_day` a day, `gap` apart (also after any
    group post already scheduled), never later than two hours before the event. None = too late to post."""
    t = start if last_paced is None else max(start, last_paced + gap)
    per: dict[Any, int] = defaultdict(int)
    slots: list[datetime | None] = []
    for _ in range(count):
        for _guard in range(400):
            local = t.astimezone(tz)
            if local.hour < DAY_START:
                t = local.replace(hour=DAY_START, minute=0, second=0, microsecond=0)
            elif local.hour >= DAY_END or per[local.date()] >= per_day:
                t = (local + timedelta(days=1)).replace(hour=DAY_START, minute=0, second=0, microsecond=0)
            else:
                break
        if event_start is not None and t > event_start - STOP_BEFORE_EVENT:
            slots.append(None)
            continue
        slots.append(t)
        per[t.astimezone(tz).date()] += 1
        t = t + gap
    return slots


class PromotionStore:
    def __init__(self, store: JobStore) -> None:
        self.store = store

    def add(
        self,
        promotion_id: int,
        *,
        kind: str,
        name: str,
        url: str = "",
        why: str = "",
        account: str = "",
        source: str = "found",
        status: str = "planned",
        note: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        stamp = iso(now())
        with self.store._connect() as db:
            cur = db.execute(
                """INSERT INTO promotion_targets (promotion_id, kind, name, url, why, account, source, status, note,
                       data, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (promotion_id, kind, name[:200], url[:1000], why[:500], account[:200], source, status, note,
                 json.dumps(data) if data else None, stamp, stamp),
            )  # fmt: skip
            return int(cur.lastrowid)

    def update(self, target_id: int, **fields: Any) -> dict[str, Any]:
        if fields:
            fields["updated_at"] = iso(now())
            columns = ", ".join(f"{k} = ?" for k in fields)
            with self.store._connect() as db:
                db.execute(f"UPDATE promotion_targets SET {columns} WHERE id = ?", [*fields.values(), target_id])
        return self.get(target_id)

    def get(self, target_id: int) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM promotion_targets WHERE id = ?", (target_id,)).fetchone()
        if row is None:
            raise LookupError(f"No place {target_id}.")
        return dict(row)

    def by_job(self, job_id: int) -> dict[str, Any] | None:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM promotion_targets WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def for_promotion(self, promotion_id: int) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM promotion_targets WHERE promotion_id = ? ORDER BY id", (promotion_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def needing_joe(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM promotion_targets WHERE status = 'needs_you' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def last_paced(self) -> datetime | None:
        """The latest group/forum post time already planned, across every promotion."""
        with self.store._connect() as db:
            row = db.execute(
                f"""SELECT MAX(scheduled_at) AS t FROM promotion_targets
                     WHERE kind IN ({", ".join("?" for _ in PACED_KINDS)}) AND scheduled_at IS NOT NULL
                       AND status NOT IN ('skipped')""",
                PACED_KINDS,
            ).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    def reusable(self, promotion_id: int) -> list[dict[str, Any]]:
        """Places that worked last time (for a cloned promotion)."""
        return [
            t
            for t in self.for_promotion(promotion_id)
            if t["status"] in DONE_OK and t["kind"] in PACED_KINDS + ("listing",)
        ]

    def counts(self, promotion_id: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        for target in self.for_promotion(promotion_id):
            counts[target["status"]] = counts.get(target["status"], 0) + 1
        return counts

    def is_finished(self, promotion_id: int) -> bool:
        targets = self.for_promotion(promotion_id)
        return bool(targets) and all(t["status"] in TERMINAL for t in targets)

    # ── views ──────────────────────────────────────────────────────────────

    @staticmethod
    def view(row: dict[str, Any]) -> dict[str, Any]:
        need = None
        if row["status"] == "needs_you":
            need = {
                "site": row.get("need_site") or "",
                "url": row.get("need_url") or "",
                "reason": row.get("need_reason") or "",
            }
        keys = ("id", "kind", "name", "url", "why", "account", "source", "status", "job_id", "scheduled_at",
                "result_url", "note", "updated_at")  # fmt: skip
        return {**{k: row.get(k) for k in keys}, "need": need}

    def promotion_view(self, job: Any) -> dict[str, Any]:
        event = (job.result or {}).get("event") if isinstance(job.result, dict) else None
        return {
            "event": event,
            "reused_from": (job.payload or {}).get("reuse_from"),
            "counts": self.counts(job.id),
            "targets": [self.view(t) for t in self.for_promotion(job.id)],
        }


# ── Joe's buttons (admin page and Telegram) ─────────────────────────────────


def need_owner(target_id: int) -> str:
    return f"need-{target_id}"


async def target_done(store: JobStore, target_id: int, browser: Any = None) -> tuple[dict[str, Any], str]:
    ps = PromotionStore(store)
    target = ps.get(target_id)
    if target["status"] != "needs_you":
        raise JobError(f"{target['name']} isn't waiting for you (it's {target['status'].replace('_', ' ')}).")
    target = ps.update(target_id, status="queued", need_until=None)
    if target["job_id"]:
        store.wake(int(target["job_id"]))
    if browser is not None:
        await browser.release(f"login:{need_owner(target_id)}")
    return target, f"Thanks. {target['name']} is back in the queue and starts as soon as the worker is free."


async def target_skip(store: JobStore, target_id: int, browser: Any = None) -> tuple[dict[str, Any], str]:
    ps = PromotionStore(store)
    target = ps.get(target_id)
    if target["status"] in TERMINAL:
        raise JobError(f"{target['name']} has already finished ({target['status'].replace('_', ' ')}).")
    if target["job_id"]:
        job = store.get(int(target["job_id"]))
        if job and job.status in ("queued", "running", "waiting_approval"):
            store.cancel(job.id)
    target = ps.update(target_id, status="skipped", note="Skipped by you.")
    if browser is not None:
        await browser.release(f"login:{need_owner(target_id)}")
    return target, f"Skipped {target['name']}."


async def target_retry(store: JobStore, target_id: int, settings: Any = None) -> tuple[dict[str, Any], str]:
    ps = PromotionStore(store)
    target = ps.get(target_id)
    if target["status"] not in ("failed", "skipped"):
        raise JobError(f"Only failed or skipped places can be tried again ({target['name']} is {target['status']}).")
    if target["kind"] == "own_social":
        if settings is None:
            raise JobError("This can't be retried here.")
        promotion = store.require(int(target["promotion_id"]))
        target = await post_own_channels_target(store, settings, promotion, target_id)
        return target, f"Your channels: {target['note']}"
    if not target["job_id"]:
        raise JobError(f"{target['name']} was never queued, so there's nothing to retry.")
    job = store.require(int(target["job_id"]))
    if job.status in ("failed", "cancelled"):
        store.retry(job.id)
    elif job.status == "done":
        # A finished job that skipped or failed the place: run it again from the start.
        with store._connect() as db:
            db.execute(
                """UPDATE agent_jobs SET status = 'queued', attempts = 0, checkpoint = NULL, result = NULL,
                       error = NULL, run_at = ?, started_at = NULL, finished_at = NULL, updated_at = ?
                 WHERE id = ?""",
                (iso(now()), iso(now()), job.id),
            )
    target = ps.update(target_id, status="queued", note=None, asks=0)
    return target, f"{target['name']} is back in the queue."


# ── own channels (Social Autopilot / Postiz) ────────────────────────────────

_HANDLE = re.compile(r"@?([A-Za-z0-9_.\-]{2,60})\s*$")


def listed_handles(post_from: str) -> set[str]:
    """Usernames Joe listed, from lines like 'Instagram: @bioluxelab' or '@bioluxelab'."""
    handles = set()
    for line in re.split(r"[\n,;]+", post_from or ""):
        line = line.strip()
        if not line:
            continue
        value = line.split(":", 1)[1] if ":" in line and not line.lower().startswith("http") else line
        value = value.strip().rstrip("/").rsplit("/", 1)[-1]
        match = _HANDLE.search(value)
        if match:
            handles.add(match.group(1).lower())
    return handles


def match_channels(channels: list[dict[str, Any]], post_from: str) -> list[dict[str, Any]]:
    """Joe's Social Autopilot channels to post on: all of them if he listed no accounts, else the ones he listed."""
    usable = [c for c in channels if c.get("enabled") and not c.get("disabled") and not c.get("missing_at")]
    handles = listed_handles(post_from)
    if not handles:
        return usable
    return [
        c
        for c in usable
        if {str(c.get("profile") or "").lower().lstrip("@"), str(c.get("name") or "").lower().lstrip("@")} & handles
    ]


def _when_text(event: dict[str, Any], tz: ZoneInfo) -> tuple[str, str]:
    start = parse_local(event.get("starts_at", ""), tz)
    if start is None:
        return "", ""
    local = start.astimezone(tz)
    long = local.strftime("%A %-d %B at %H:%M").replace(":00", "")
    return long, local.strftime("%a %-d %b, %H:%M")


async def post_own_channels_target(store: JobStore, settings: Any, promotion: Any, target_id: int) -> dict[str, Any]:
    """Create the Social Autopilot posts for a promotion (one soon, one the day before), reviewed by AI."""
    from task_agent.social import SocialClient, SocialError

    ps = PromotionStore(store)
    target = ps.get(target_id)
    event = (promotion.result or {}).get("event") or {}
    payload = promotion.payload or {}
    channel_ids = list(json.loads(target.get("data") or "{}").get("channel_ids") or [])
    tz = ZoneInfo(settings.store_timezone)
    long_when, short_when = _when_text(event, tz)
    where = "online" if event.get("online") else ", ".join(p for p in (event.get("venue"), event.get("town")) if p)
    link = event.get("ticket_url") or next(iter(payload.get("event_urls") or []), "")
    tag = " ".join((payload.get("tag") or "").split())
    brief = (
        f"Promote our event '{event.get('title', '')}'"
        + (f" on {long_when}" if long_when else "")
        + (f", {where}" if where else "")
        + f". Price: {event.get('price') or 'see the link'}. {str(event.get('description') or '')[:700]} "
        + (f"Mention or tag: {tag}. " if tag else "")
        + "Invite people to book or find out more with the link."
    )
    body: dict[str, Any] = {
        "content_type": "holiday",
        "template_key": "holiday_greeting",
        "channel_ids": channel_ids,
        "brief": brief[:2000],
        "texts": {
            "occasion": str(event.get("title", ""))[:70],
            "subheadline": " · ".join(p for p in (short_when, where) if p)[:90],
        },
        "link_url": link,
        "approval_mode": "ai_review",
        "publish_mode": "schedule",
    }
    social = SocialClient(settings)
    notes: list[str] = []
    try:
        first = await social.post("/posts", body, http_timeout=60)
        notes.append(f"post planned for {_fmt(first.get('scheduled_at'), tz)}")
        start = parse_local(event.get("starts_at", ""), tz)
        if start is not None and start - now() > timedelta(hours=48):
            eve = (start.astimezone(tz) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
            reminder = {**body, "scheduled_at": int(eve.timestamp()), "brief": "Reminder: it's tomorrow. " + brief}
            second = await social.post("/posts", reminder, http_timeout=60)
            notes.append(f"reminder for {_fmt(second.get('scheduled_at'), tz)}")
    except SocialError as exc:
        status = "posted" if notes else "failed"
        return ps.update(target_id, status=status, note=("; ".join(notes) + "; " if notes else "") + str(exc)[:300])
    return ps.update(
        target_id, status="posted", note=f"Social Autopilot: {' and '.join(notes)} (AI-reviewed before posting)."
    )


def _fmt(epoch: Any, tz: ZoneInfo) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), tz).strftime("%a %-d %b %H:%M")
    except (TypeError, ValueError):
        return "the next free slot"


# ── event picture ───────────────────────────────────────────────────────────


async def download_image(url: str, folder: Path) -> str:
    """Save the event picture for listing sites' upload boxes. Returns its path, or '' if it couldn't be fetched."""
    from task_agent.tools.web import USER_AGENT, BlockedURL, check_public_url

    if not url.startswith(("http://", "https://")):
        return ""
    try:
        await check_public_url(url)
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
            response = await client.get(url)
    except (BlockedURL, httpx.HTTPError):
        return ""
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.status_code >= 400 or not kind.startswith("image/") or len(response.content) > MAX_IMAGE_BYTES:
        return ""
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(kind) or (
        mimetypes.guess_extension(kind) or ".jpg"
    )
    path = folder / f"event{ext}"
    await asyncio.to_thread(_save, path, response.content)
    return str(path)


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
