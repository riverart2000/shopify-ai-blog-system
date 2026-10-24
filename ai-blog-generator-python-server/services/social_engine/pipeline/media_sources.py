"""Postiz creative sources used while generating a post.

* `ai_background`   — Postiz AI scene (seasonal/lifestyle backdrop, no product,
                      no text) that our templates composite the REAL product onto.
* `postiz_design`   — a design from the Postiz media library (made with the
                      Postiz design editor templates), rotated by file-name search.
* `ai_video`        — a Postiz AI video built from the product photos
                      ("seedance") or slides + ElevenLabs voice; polled until ready.
* `tiktok_music`    — a track from TikTok's commercial library via Postiz.

Every source falls back cleanly: when credits are used up, the environment
isn't live, or Postiz fails, generation continues with our own templates and
the reason is recorded on the post.
"""
from __future__ import annotations

import logging
import mimetypes
import time
from dataclasses import dataclass, field
from typing import Any

from db.social_engine import observability
from db.social_engine import posts as post_store

from ..activity import record
from ..context import StoreContext
from ..errors import Deferred, SocialEngineError
from ..http import ServiceClient

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")

_VIDEO_POLL_SECONDS = 45
_VIDEO_TIMEOUT_SECONDS = 25 * 60


@dataclass
class SourceNotes:
    warnings: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)


async def _download(sctx: StoreContext, url: str, *, post_id: str, fmt: str, position: int,
                    default_ext: str) -> tuple[str, int, str, str]:
    """Fetch a Postiz-hosted file into local media storage (for previews and probing)."""
    from ..creatives.storage import save_media_bytes

    async with ServiceClient("cdn", "", timeout=300.0) as client:
        response = await client.request("GET", url, operation="postiz.media_download", ctx=sctx.call_context(post_id))
    mime = response.headers.get("content-type", "").split(";")[0].strip() or mimetypes.guess_type(url)[0] or ""
    ext = (mimetypes.guess_extension(mime) or default_ext).lstrip(".")
    ext = {"jpe": "jpg", "jpeg": "jpg"}.get(ext, ext)
    stored = save_media_bytes(sctx.store_id, post_id, fmt, position, ext, response.content)
    return stored.path, stored.bytes, stored.sha256, mime or f"image/{ext}"


def background_prompt(theme_key: str, products: list[dict], angle: str = "") -> str:
    from ..campaigns.themes import get_theme

    theme = get_theme(theme_key or "custom")
    category = ""
    if products:
        product = products[0]
        category = product.get("product_type") or ", ".join(c.get("title", "") for c in product.get("collections") or [])[:60]
    base = theme.background_prompt or ("Photorealistic premium wellness lifestyle scene with soft natural light, "
                                       "empty space in the centre, no people, no text, no logos, no products")
    extra = f" Mood inspired by: {angle}." if angle else ""
    context = f" Setting suits a {category.lower()} product." if category else ""
    return f"{base}.{context}{extra} Square-friendly composition, high detail, no packaging, no lettering."


async def ai_background(sctx: StoreContext, *, post: dict, products: list[dict], notes: SourceNotes) -> str | None:
    """Generate (once per post) a Postiz AI backdrop and return its local path."""
    from ..publishers.postiz_tools import get_creative_tools

    options = post.get("options") or {}
    cached = options.get("ai_background_path")
    if cached:
        return str(cached)
    tools = get_creative_tools(sctx, correlation_id=post["id"])
    try:
        generated = await tools.generate_image(background_prompt(options.get("theme_key", ""), products, post.get("angle", "")))
        path, _, _, _ = await _download(sctx, generated.url, post_id=post["id"], fmt="background", position=0,
                                        default_ext=".jpg")
    except SocialEngineError as exc:
        notes.warnings.append(f"Postiz AI background skipped: {exc.message}")
        return None
    finally:
        await tools.aclose()
    await post_store.update_post(sctx.store_id, post["id"], options={**options, "ai_background_path": path})
    notes.used.append("postiz_ai_image")
    return path


async def postiz_design(sctx: StoreContext, *, post: dict, search: str, notes: SourceNotes) -> dict | None:
    """Pick a design from the Postiz media library (rotating) and download it for previews."""
    from ..publishers.postiz_tools import get_creative_tools

    tools = get_creative_tools(sctx, correlation_id=post["id"])
    try:
        library = await tools.media_library(search=search)
    except SocialEngineError as exc:
        notes.warnings.append(f"Postiz media library unavailable: {exc.message}")
        return None
    finally:
        await tools.aclose()
    items = [i for i in library["items"] if i.get("path")]
    if not items:
        notes.warnings.append(f"No Postiz designs match '{search}' — used a template instead.")
        return None
    key = f"postiz_design_rotation:{sctx.store_id}:{search.lower()}"
    index = int(await observability.get_state(key, 0) or 0)
    await observability.set_state(key, index + 1)
    item = items[index % len(items)]
    path, size, sha, mime = await _download(sctx, item["path"], post_id=post["id"], fmt="design", position=0,
                                            default_ext=".jpg")
    notes.used.append("postiz_design")
    return {"path": path, "bytes": size, "sha256": sha, "mime": mime, "external_id": str(item.get("id", "")),
            "external_path": item["path"], "name": item.get("name", "")}


async def ai_video(sctx: StoreContext, *, post: dict, products: list[dict], notes: SourceNotes) -> dict | None:
    """Start or poll a Postiz AI video. Raises Deferred while Postiz is rendering.

    Returns an asset dict when ready, or None when the caller should fall back
    to our own ffmpeg product video.
    """
    from ..publishers.guard import live_publishing_enabled
    from ..publishers.postiz_tools import get_creative_tools

    options = dict(post.get("options") or {})
    state = dict(options.get("ai_video") or {})
    if not live_publishing_enabled():
        notes.warnings.append("Postiz AI video is only generated on the live system; used a template video.")
        return None
    tools = get_creative_tools(sctx, correlation_id=post["id"])
    try:
        if not state.get("job_id") and not state.get("url"):
            images: list[dict] = []
            for product in products[:1]:
                for url in [img.get("url") for img in product.get("images") or [] if img.get("url")][:3]:
                    images.append(await tools.upload_from_url(url))
            title = products[0]["title"] if products else post.get("title", "")
            prompt = (f"Short premium vertical product video for {title}. "
                      f"{post.get('angle') or 'Calm, natural-light wellness mood'}. Slow elegant camera movement, "
                      "the product must look exactly like the reference images, no text overlays, no people's faces.")
            generator = "seedance" if images else "image-text-slides"
            started = await tools.start_video(generator=generator, prompt=prompt, orientation="vertical",
                                              images=images or None, voice=sctx.settings.postiz.video_voice_id)
            state = {"job_id": started.job_id, "url": started.url, "external_id": started.external_id,
                     "started_at": int(time.time()), "generator": generator}
            options["ai_video"] = state
            await post_store.update_post(sctx.store_id, post["id"], options=options)
            await record(sctx.store_id, "post", post["id"], f"Postiz AI video started ({generator})", stage="generate")
        if not state.get("url"):
            status = await tools.video_status(state["job_id"])
            if status.status == "failed":
                notes.warnings.append(f"Postiz AI video failed: {status.error or 'no reason given'}; used a template video.")
                return None
            if status.status != "ready":
                if time.time() - int(state.get("started_at") or 0) > _VIDEO_TIMEOUT_SECONDS:
                    notes.warnings.append("Postiz AI video took too long; used a template video.")
                    return None
                raise Deferred("Waiting for Postiz to finish the AI video.", run_at=int(time.time()) + _VIDEO_POLL_SECONDS)
            state.update(url=status.url, external_id=status.external_id)
            options["ai_video"] = state
            await post_store.update_post(sctx.store_id, post["id"], options=options)
    except Deferred:
        raise
    except SocialEngineError as exc:
        notes.warnings.append(f"Postiz AI video unavailable: {exc.message}; used a template video.")
        return None
    finally:
        await tools.aclose()

    path, size, sha, mime = await _download(sctx, state["url"], post_id=post["id"], fmt="story", position=0,
                                            default_ext=".mp4")
    width = height = duration_ms = 0
    try:
        from ..creatives.video import probe_video

        info = await probe_video(path)
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        duration_ms = int(float(info.get("duration") or 0) * 1000)
    except Exception:  # probing is informational
        logger.debug("Could not probe Postiz video", exc_info=True)
    notes.used.append("postiz_ai_video")
    return {"path": path, "bytes": size, "sha256": sha, "mime": mime or "video/mp4", "kind": "video",
            "format": "story", "width": width, "height": height, "duration_ms": duration_ms,
            "external_id": state.get("external_id", ""), "external_path": state["url"],
            "source": f"postiz:{state.get('generator', 'ai_video')}"}


async def tiktok_music(sctx: StoreContext, channel_id: str, *, correlation_id: str = "") -> dict | None:
    """A commercial-library track for TikTok Business videos (cached for a day)."""
    from ..publishers import get_publisher

    key = f"tiktok_music:{sctx.store_id}:{channel_id}"
    cached, updated = await observability.get_state_with_time(key)
    if cached and updated and time.time() - updated < 86400:
        tracks = cached
    else:
        publisher = get_publisher(sctx, correlation_id=correlation_id)
        try:
            raw = await publisher.run_channel_tool(channel_id, "musicSearch", {"genre": ""})
        except SocialEngineError:
            return None
        finally:
            await publisher.aclose()
        tracks = raw if isinstance(raw, list) else (raw or {}).get("output") or (raw or {}).get("tracks") or []
        tracks = [t for t in tracks if isinstance(t, dict) and t.get("id")][:20]
        await observability.set_state(key, tracks)
    if not tracks:
        return None
    pick = tracks[int(time.time() // 3600) % len(tracks)]
    return {"id": str(pick["id"]), "title": pick.get("title", ""), "artist": pick.get("artist", ""),
            "image": pick.get("image", ""), "audio_volume": 100, "video_volume": 0}
