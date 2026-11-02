"""Founder videos pipeline.

Two ways in:

* a video file (dropped in the watched folder on the Mac, or uploaded in the app)
  → copied into media storage → a `founder_video` post publishes it in full on
  the channels in `founder_videos.full_video_providers` (YouTube by default)
  → once Postiz reports the YouTube URL, clipping starts;
* a YouTube URL pasted in the app → clipping starts straight away.

Postiz Cloud can only clip YouTube videos, which is why a file always goes
through YouTube first. Each finished clip becomes a `founder_clip` post on the
channels in `clip_providers`, spaced `clip_days_apart` days apart, through the
normal caption → approval → publish pipeline.

State machine (social_founder_videos.status):
  new → awaiting_youtube → clipping → done
                        ↘ failed (error says why; "retry" puts it back)
"""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db.social_engine import config_store
from db.social_engine import founder as store
from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ..activity import record
from ..context import StoreContext, load_store_context
from ..creatives.storage import file_digest, media_root, safe_segment
from ..errors import ExternalServiceError, RateLimited, SocialEngineError, ValidationError
from ..jobs.queue import enqueue
from ..models import ContentType, MediaFormat
from ..scheduling.slots import SlotPlanner

logger = logging.getLogger("ai_blog_server.social_engine.founder")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
STABLE_SECONDS = 90          # a file must be untouched this long before it is picked up (phone still copying)
POLL_SECONDS = 300           # how often an in-progress clipping is checked
BUSY_RETRY_SECONDS = 900     # Postiz runs one clipping at a time
PROCESSED_DIR = "Processed"
README_NAME = "READ ME — how this folder works.txt"

_YOUTUBE_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,})"
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "and", "for", "with", "from", "this", "that", "after", "before", "video", "founder", "review",
         "using", "how", "why", "what", "my", "our", "your", "set", "new", "mov", "mp4", "img", "clip"}


# ------------------------------------------------------------------ helpers

def youtube_video_id(url: str) -> str:
    match = _YOUTUBE_RE.match(str(url or "").strip())
    return match.group(1) if match else ""


def canonical_youtube_url(url: str) -> str:
    video_id = youtube_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


def humanise_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"^(IMG|VID|MOV|PXL|Screen Recording)[ _-]*\d*[ _-]*", "", stem, flags=re.I)
    stem = re.sub(r"[_\-\.]+", " ", stem).strip()
    return (stem[:1].upper() + stem[1:]) if stem else "Founder video"


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(str(text or "").lower()) if len(w) > 2 and w not in _STOP}


def match_products(products: list[dict], text: str, *, limit: int = 1) -> list[str]:
    """Products whose title words appear in `text` (a filename or note). Needs ≥2 matching words,
    or one word when the title has a single distinctive word, so "massage gun after a run" →
    the massage gun and "IMG_4411" → nothing."""
    words = _tokens(text)
    if not words:
        return []
    scored: list[tuple[int, float, str]] = []
    for product in products:
        title_words = _tokens(product.get("title", ""))
        if not title_words:
            continue
        hits = len(title_words & words)
        needed = 1 if len(title_words) == 1 else 2
        if hits >= needed:
            scored.append((hits, hits / len(title_words), str(product["product_id"])))
    scored.sort(reverse=True)
    return [pid for _, _, pid in scored[:limit]]


def drop_folder(settings) -> Path:
    folder = Path(os.path.expanduser(settings.founder_videos.drop_folder)).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PROCESSED_DIR).mkdir(exist_ok=True)
    readme = folder / README_NAME
    if not readme.exists():
        readme.write_text(
            "Drop a video here and the Social Autopilot takes it from there.\n\n"
            "1. Name the file after what it is about, e.g. 'massage gun after a run.mov' or\n"
            "   'why I wear the wide toe box shoes.mp4'. The product is matched from the words.\n"
            "2. Within about five minutes the video is picked up, moved into 'Processed', and a full-length\n"
            "   post for YouTube appears in the approval queue (Social Autopilot → Approval queue).\n"
            "3. Once it is live on YouTube, Postiz cuts it into short clips. Each clip appears in the\n"
            "   approval queue for TikTok, Instagram, YouTube Shorts, Facebook and Pinterest, one a day.\n\n"
            "Keep recordings under 40 minutes (the Postiz plan has 120 clipping minutes a month).\n"
            "Already on YouTube? Paste the link in the app instead: Social Autopilot → Founder videos.\n",
            encoding="utf-8",
        )
    return folder


def _founder_dir(store_id: str, video_id: str) -> Path:
    path = media_root() / safe_segment(store_id) / "founder" / safe_segment(video_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _probe(path: Path) -> dict:
    try:
        from ..creatives.video import probe_video

        info = await probe_video(path)
        return {"width": int(info.get("width") or 0), "height": int(info.get("height") or 0),
                "duration_ms": int(float(info.get("duration") or 0) * 1000)}
    except Exception as exc:  # ffprobe missing or an odd container: still usable, just unmeasured
        logger.debug("Could not probe %s: %s", path, exc)
        return {"width": 0, "height": 0, "duration_ms": 0}


async def _usable_channels(sctx: StoreContext, providers: list[str]) -> list[dict]:
    wanted = set(providers)
    return [c for c in await config_store.list_channels(sctx.store_id, usable_only=True) if c["provider"] in wanted]


async def _plan_times(sctx: StoreContext, channels: list[dict], *, earliest: datetime | None = None) -> dict[str, int]:
    """Next free slot for these channels at or after `earliest` (default: now)."""
    now_ts = int(time.time())
    existing = await post_store.list_targets(
        sctx.store_id, statuses=("pending", "publishing", "uncertain", "scheduled", "published", "draft"),
        scheduled_from=now_ts - 86400, scheduled_to=now_ts + 30 * 86400,
    )
    existing += await post_store.planned_channel_times(sctx.store_id, scheduled_from=now_ts - 86400,
                                                       scheduled_to=now_ts + 30 * 86400)
    planner = SlotPlanner(sctx.settings.schedule, channels, existing)
    kwargs: dict[str, Any] = {"earliest": earliest} if earliest else {}
    times: dict[str, int] = {}
    for when, cids in planner.plan([str(c["channel_id"]) for c in channels], **kwargs):
        times.update({cid: int(when.timestamp()) for cid in cids})
    return times


def _link_for(sctx: StoreContext, products: list[dict]) -> str:
    if len(products) == 1:
        return products[0].get("url") or sctx.product_url(products[0].get("handle", ""))
    return sctx.storefront_url


# ------------------------------------------------------------------ intake

async def ingest_file(
    sctx: StoreContext, path: str | Path, *, source: str, note: str = "", product_ids: list[str] | None = None,
    original_name: str = "", move: bool = False,
) -> dict:
    """Take a video file into the engine and queue its full-length post. Returns the row."""
    src = Path(path)
    if not src.is_file():
        raise ValidationError(f"{src.name} is not a file.")
    if src.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValidationError(f"{src.name}: only video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) are accepted.")
    settings = sctx.settings.founder_videos
    sha256, size = file_digest(src)
    if size == 0:
        raise ValidationError(f"{src.name} is empty.")
    duplicate = await store.find_by_sha(sctx.store_id, sha256)
    if duplicate:
        raise ValidationError(f"This video was already added on {time.strftime('%d %b', time.localtime(duplicate['created_at']))} "
                              f"(status: {duplicate['status']}).")
    name = original_name or src.name
    title = humanise_name(note or name)
    video_id = f"fv_{sha256[:12]}"
    dest = _founder_dir(sctx.store_id, video_id) / f"original{src.suffix.lower()}"
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    probe = await _probe(dest)
    if not product_ids:
        catalogue = await product_store.list_products(sctx.store_id, available_only=True, limit=500)
        product_ids = match_products(catalogue, f"{name} {note}")
    row = {
        "id": video_id, "source": source, "title": title, "note": note.strip(), "original_name": name,
        "original_path": str(dest), "sha256": sha256, "bytes": size, **probe,
        "product_ids": list(product_ids or []), "status": "new",
    }
    minutes = probe["duration_ms"] / 60000
    if minutes > settings.max_source_minutes:
        row.update(status="failed", error=f"{minutes:.0f} minutes is longer than the {settings.max_source_minutes}-minute limit "
                                          f"(the Postiz plan has 120 clipping minutes a month). Trim it and drop it again.")
        await store.create_video(sctx.store_id, row)
        await record(sctx.store_id, "founder_video", video_id, row["error"], level="warning", stage="founder")
        return (await store.get_video(sctx.store_id, video_id)) or row
    await store.create_video(sctx.store_id, row)
    await record(sctx.store_id, "founder_video", video_id,
                 f"Founder video added from {source}: {title} ({minutes:.1f} min)", stage="founder",
                 data={"product_ids": row["product_ids"]})
    await _publish_full_video(sctx, await store.get_video(sctx.store_id, video_id) or row)
    return (await store.get_video(sctx.store_id, video_id)) or row


async def ingest_url(sctx: StoreContext, url: str, *, note: str = "", product_ids: list[str] | None = None) -> dict:
    """A video already on YouTube: go straight to clipping."""
    canonical = canonical_youtube_url(url)
    if not canonical:
        raise ValidationError("That is not a YouTube link. Paste the video's youtube.com or youtu.be URL.")
    existing = await store.find_by_youtube_url(sctx.store_id, canonical)
    if existing:
        raise ValidationError(f"That video was already added (status: {existing['status']}).")
    if not product_ids:
        catalogue = await product_store.list_products(sctx.store_id, available_only=True, limit=500)
        product_ids = match_products(catalogue, note)
    video_id = f"fv_{youtube_video_id(canonical)}"
    row = {
        "id": video_id, "source": "url", "title": humanise_name(note) if note else f"YouTube {youtube_video_id(canonical)}",
        "note": note.strip(), "youtube_url": canonical, "product_ids": list(product_ids or []),
        "status": "awaiting_youtube", "next_check_at": int(time.time()),
    }
    if await store.get_video(sctx.store_id, video_id):
        raise ValidationError("That video was already added.")
    await store.create_video(sctx.store_id, row)
    await record(sctx.store_id, "founder_video", video_id, f"YouTube video added for clipping: {canonical}",
                 stage="founder", data={"product_ids": row["product_ids"]})
    await enqueue("founder.scan", {"reason": "url"}, store_id=sctx.store_id, dedupe_key=f"founder:scan:{sctx.store_id}",
                  priority=6)
    return (await store.get_video(sctx.store_id, video_id)) or row


async def _publish_full_video(sctx: StoreContext, video: dict) -> None:
    settings = sctx.settings.founder_videos
    channels = await _usable_channels(sctx, settings.full_video_providers)
    if not channels:
        await store.update_video(sctx.store_id, video["id"], status="failed",
                                 error="No YouTube channel is connected and enabled, so the video cannot be posted "
                                       "(Postiz clips YouTube videos only). Connect YouTube in Postiz and retry.")
        return
    times = await _plan_times(sctx, channels)
    if not times:
        await store.update_video(sctx.store_id, video["id"], status="failed",
                                 error="No free posting slot in the next month for the full video.")
        return
    products = await product_store.get_products(sctx.store_id, video.get("product_ids") or [])
    products = [p for p in products if p.get("available") and not p.get("deleted_at")]
    mime = mimetypes.guess_type(video["original_path"])[0] or "video/mp4"
    premade = [{
        "kind": "video", "format": MediaFormat.STORY, "path": video["original_path"], "mime": mime,
        "bytes": int(video.get("bytes") or 0), "sha256": video.get("sha256", ""), "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0), "duration_ms": int(video.get("duration_ms") or 0),
        "source": f"founder_{video.get('source', 'upload')}",
    }]
    channel_settings = {str(c["channel_id"]): {"type": settings.youtube_visibility}
                        for c in channels if c["provider"] == "youtube"}
    post_id, _ = await post_store.create_post(sctx.store_id, {
        "rule_type": "founder_video", "content_type": ContentType.FOUNDER_VIDEO, "title": video["title"],
        "product_ids": [str(p["product_id"]) for p in products], "status": "planned",
        "brief": (f"A video recorded by the founder: {video['title']}. {video.get('note', '')} "
                  "Write the description in the founder's own voice (first person), say what the video shows and "
                  "why it matters for the viewer, then invite them to the link.").strip(),
        "approval_mode": settings.approval_mode, "publish_mode": "schedule",
        "scheduled_at": min(times.values()),
        "dedupe_key": f"founder:full:{video['id']}",
        "options": {
            "channel_ids": list(times), "channel_times": times, "premade_media": premade,
            "founder_video_id": video["id"], "link_override": _link_for(sctx, products),
            "channel_settings": channel_settings,
            "texts": {"founder_note": video.get("note", ""), "founder_title": video["title"]},
        },
    })
    await store.update_video(sctx.store_id, video["id"], status="awaiting_youtube", full_post_id=post_id,
                             next_check_at=int(time.time()) + POLL_SECONDS, error="")
    await enqueue("post.generate", {"post_id": post_id}, store_id=sctx.store_id, dedupe_key=f"generate:{post_id}",
                  correlation_id=post_id, priority=9)
    await record(sctx.store_id, "post", post_id, f"Planned the full founder video for {', '.join(c['provider'] for c in channels)}",
                 stage="plan", data={"founder_video_id": video["id"]})


# ------------------------------------------------------------------ progress

def _postiz_client(sctx: StoreContext, correlation_id: str = ""):
    from ..publishers.guard import LIVE_PUBLISHING_ENV, live_publishing_enabled
    from ..publishers.postiz_client import PostizClient

    if not live_publishing_enabled():
        raise SocialEngineError(f"Postiz clipping spends plan minutes and only runs on the live system ({LIVE_PUBLISHING_ENV}=1).")
    api_key = sctx.settings.postiz.api_key()
    if not api_key:
        raise SocialEngineError("Postiz API key missing (POSTIZ_API_KEY).")
    return PostizClient(api_key, base_url=sctx.settings.postiz.base_url, ctx=sctx.call_context(correlation_id))


async def _check_full_video(sctx: StoreContext, video: dict) -> dict:
    """While the full video is in the pipeline: pick up its YouTube URL (or its failure)."""
    post = await post_store.get_post(sctx.store_id, video["full_post_id"])
    if not post:
        await store.update_video(sctx.store_id, video["id"], status="failed", error="The full-video post was deleted.")
        return {"state": "failed"}
    if post["status"] in ("failed", "rejected", "cancelled"):
        reason = post.get("rejected_reason") or post.get("error_message") or post["status"]
        await store.update_video(sctx.store_id, video["id"], status="failed",
                                 error=f"The full video was not published ({reason}). Fix the post or retry.")
        return {"state": "failed"}
    for target in await post_store.list_targets_for_post(post["id"]):
        if target.get("provider") == "youtube" and target.get("release_url") and youtube_video_id(target["release_url"]):
            url = canonical_youtube_url(target["release_url"])
            await store.update_video(sctx.store_id, video["id"], youtube_url=url, next_check_at=int(time.time()))
            await record(sctx.store_id, "founder_video", video["id"], f"Live on YouTube: {url}", stage="founder")
            return {"state": "youtube_url"}
    await store.update_video(sctx.store_id, video["id"], next_check_at=int(time.time()) + POLL_SECONDS)
    return {"state": "waiting_for_youtube", "post_status": post["status"]}


async def _start_clipping(sctx: StoreContext, video: dict) -> dict:
    settings = sctx.settings.founder_videos
    client = _postiz_client(sctx, video["id"])
    try:
        clipping_id = await client.start_clipping(video["youtube_url"], clips=settings.clips_per_video, fit=settings.fit)
    except ExternalServiceError as exc:
        status = exc.status_code or 0
        if status == 429:   # one clipping at a time / daily cap: try later
            await store.update_video(sctx.store_id, video["id"], next_check_at=int(time.time()) + BUSY_RETRY_SECONDS,
                                     error="Postiz is busy with another clipping; trying again shortly.")
            return {"state": "busy"}
        if status in (402, 406):
            await store.update_video(sctx.store_id, video["id"], status="failed",
                                     error="Postiz has no clipping minutes left this month (120 on the Team plan). "
                                           "Retry after the monthly reset, or upgrade.")
            return {"state": "failed"}
        if status and status < 500:
            await store.update_video(sctx.store_id, video["id"], status="failed", error=f"Postiz refused the clipping: {exc}")
            return {"state": "failed"}
        await store.update_video(sctx.store_id, video["id"], next_check_at=int(time.time()) + POLL_SECONDS,
                                 error=f"Postiz error, will retry: {exc}")
        return {"state": "retry"}
    finally:
        await client.aclose()
    await store.update_video(sctx.store_id, video["id"], status="clipping", clipping_id=clipping_id, error="",
                             next_check_at=int(time.time()) + POLL_SECONDS)
    await record(sctx.store_id, "founder_video", video["id"],
                 f"Postiz clipping started ({settings.clips_per_video} clips, {settings.fit})", stage="founder")
    return {"state": "clipping"}


async def _download(sctx: StoreContext, url: str, *, video_id: str, position: int, default_ext: str) -> dict:
    from ..pipeline.media_sources import _download as fetch

    path, size, sha, mime = await fetch(sctx, url, post_id=f"founder-{video_id}", fmt="story", position=position,
                                        default_ext=default_ext)
    return {"path": path, "bytes": size, "sha256": sha, "mime": mime}


async def _finish_clipping(sctx: StoreContext, video: dict) -> dict:
    settings = sctx.settings.founder_videos
    client = _postiz_client(sctx, video["id"])
    try:
        clipping = await client.get_clipping(video["clipping_id"])
    finally:
        await client.aclose()
    status = str(clipping.get("status") or "")
    if status == "failed":
        await store.update_video(sctx.store_id, video["id"], status="failed",
                                 error=f"Postiz clipping failed: {clipping.get('error') or 'no reason given'}")
        return {"state": "failed"}
    if status != "completed":
        await store.update_video(sctx.store_id, video["id"], next_check_at=int(time.time()) + POLL_SECONDS)
        return {"state": "clipping", "postiz_status": status}
    clips = [c for c in clipping.get("clips") or [] if c.get("path") and str(c.get("status") or "completed") == "completed"]
    if not clips:
        await store.update_video(sctx.store_id, video["id"], status="failed",
                                 error="Postiz finished but produced no clips (minutes are given back). Try a longer or clearer recording.")
        return {"state": "failed"}
    channels = await _usable_channels(sctx, settings.clip_providers)
    if not channels:
        await store.update_video(sctx.store_id, video["id"], status="failed", clips=clips,
                                 error="No clip channel is connected and enabled (TikTok, Instagram, YouTube, Facebook, Pinterest).")
        return {"state": "failed"}
    products = await product_store.get_products(sctx.store_id, video.get("product_ids") or [])
    products = [p for p in products if p.get("available") and not p.get("deleted_at")]
    link = _link_for(sctx, products)
    post_ids: list[str] = []
    previous: datetime | None = None      # clips go out in order, `clip_days_apart` days apart
    for index, clip in enumerate(clips):
        dedupe = f"founder:clip:{video['id']}:{clip.get('id') or index}"
        if await post_store.get_post_by_dedupe(sctx.store_id, dedupe):
            continue
        media = await _download(sctx, str(clip["path"]), video_id=video["id"], position=index, default_ext=".mp4")
        probe = await _probe(Path(media["path"]))
        thumbnail = None
        if clip.get("thumbnail"):
            try:
                thumbnail = await _download(sctx, str(clip["thumbnail"]), video_id=video["id"], position=100 + index,
                                            default_ext=".jpg")
            except SocialEngineError:
                thumbnail = None
        earliest = previous + timedelta(days=settings.clip_days_apart, minutes=1) if previous else None
        times = await _plan_times(sctx, channels, earliest=earliest)
        if not times:
            logger.warning("No slot for founder clip %s of %s", index + 1, video["id"])
            continue
        previous = datetime.fromtimestamp(min(times.values()), tz=timezone.utc)
        premade = [{
            "kind": "video", "format": MediaFormat.STORY, **media, **probe,
            "external_id": str(clip.get("mediaId") or ""), "external_path": str(clip["path"]),
            "source": "postiz_clipping", "thumbnail": thumbnail,
        }]
        clip_title = str(clip.get("title") or f"{video['title']} — clip {index + 1}")[:120]
        post_id, _ = await post_store.create_post(sctx.store_id, {
            "rule_type": "founder_clip", "content_type": ContentType.FOUNDER_CLIP, "title": clip_title,
            "product_ids": [str(p["product_id"]) for p in products], "status": "planned",
            "brief": (f"Short clip {index + 1} of {len(clips)} cut from a video the founder recorded ({video['title']}). "
                      f"What this clip shows: {clip.get('title') or ''}. {clip.get('content') or ''} "
                      f"{video.get('note', '')} Write it in the founder's own voice, first person, one idea, "
                      "hook first.").strip(),
            "approval_mode": settings.approval_mode, "publish_mode": "schedule",
            "scheduled_at": min(times.values()), "dedupe_key": dedupe,
            "options": {
                "channel_ids": list(times), "channel_times": times, "premade_media": premade,
                "founder_video_id": video["id"], "founder_clip_index": index, "link_override": link,
                "texts": {"clip_title": str(clip.get("title") or ""), "clip_summary": str(clip.get("content") or ""),
                          "founder_note": video.get("note", ""), "founder_title": video["title"]},
            },
        })
        post_ids.append(post_id)
        await enqueue("post.generate", {"post_id": post_id}, store_id=sctx.store_id, dedupe_key=f"generate:{post_id}",
                      correlation_id=post_id, priority=8)
    await store.update_video(sctx.store_id, video["id"], status="done", clips=clips, clip_post_ids=post_ids, error="",
                             next_check_at=None)
    await record(sctx.store_id, "founder_video", video["id"],
                 f"{len(post_ids)} clip post(s) planned from {len(clips)} Postiz clip(s)", stage="founder",
                 data={"post_ids": post_ids})
    return {"state": "done", "clips": len(clips), "posts": len(post_ids)}


async def advance(sctx: StoreContext, *, force: bool = False) -> dict:
    """Move every in-progress video one step on. Safe to call often."""
    summary: dict[str, int] = {}
    now_ts = int(time.time())
    for video in await store.list_videos(sctx.store_id, statuses=("awaiting_youtube", "clipping")):
        if not force and video.get("next_check_at") and int(video["next_check_at"]) > now_ts:
            continue
        try:
            if video["status"] == "awaiting_youtube" and not video.get("youtube_url"):
                result = await _check_full_video(sctx, video)
                if result["state"] != "youtube_url":
                    summary[result["state"]] = summary.get(result["state"], 0) + 1
                    continue
                video = await store.get_video(sctx.store_id, video["id"]) or video
            if video["status"] == "awaiting_youtube":
                result = await _start_clipping(sctx, video)
            else:
                result = await _finish_clipping(sctx, video)
        except RateLimited as exc:
            await store.update_video(sctx.store_id, video["id"], next_check_at=exc.retry_at, error=str(exc))
            result = {"state": "rate_limited"}
        except SocialEngineError as exc:
            await store.update_video(sctx.store_id, video["id"], next_check_at=now_ts + POLL_SECONDS, error=str(exc))
            logger.warning("Founder video %s: %s", video["id"], exc)
            result = {"state": "error"}
        summary[result["state"]] = summary.get(result["state"], 0) + 1
    return summary


async def scan_drop_folder(sctx: StoreContext) -> dict:
    """Pick up finished video files from the watched folder."""
    settings = sctx.settings.founder_videos
    if not settings.enabled:
        return {"skipped": "disabled"}
    folder = drop_folder(sctx.settings)
    processed = folder / PROCESSED_DIR
    added, skipped, errors = 0, 0, []
    for entry in sorted(folder.iterdir()):
        if not entry.is_file() or entry.name.startswith(".") or entry.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        stat = entry.stat()
        if time.time() - stat.st_mtime < STABLE_SECONDS or stat.st_size == 0:
            skipped += 1
            continue
        try:
            row = await ingest_file(sctx, entry, source="folder", original_name=entry.name, move=True)
            added += 1
            # Leave a marker so the owner can see it was taken (the file itself now lives in media storage).
            (processed / f"{entry.name} → {row['status']}.txt").write_text(
                f"Taken by Social Autopilot on {time.strftime('%d %b %Y %H:%M')} as {row['id']}.\n"
                f"Status: {row['status']}. {row.get('error', '')}\n", encoding="utf-8")
        except ValidationError as exc:
            errors.append(f"{entry.name}: {exc}")
            target = processed / entry.name
            if target.exists():
                target = processed / f"{entry.stem}-{int(time.time())}{entry.suffix}"
            shutil.move(str(entry), str(target))
            (processed / f"{entry.name} → not taken.txt").write_text(f"{exc}\n", encoding="utf-8")
        except Exception as exc:  # keep scanning the rest; the file stays for the next run
            errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
            logger.exception("Founder video intake failed for %s", entry)
    return {"folder": str(folder), "added": added, "waiting": skipped, "errors": errors}


async def run_cycle(store_id: str, *, force: bool = False) -> dict:
    sctx = await load_store_context(store_id, correlation_id="founder")
    scan = await scan_drop_folder(sctx)
    progress = await advance(sctx, force=force)
    return {"scan": scan, "progress": progress}
