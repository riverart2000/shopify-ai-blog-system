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


def background_prompt(theme_key: str, products: list[dict], angle: str = "", *, style: str = "",
                      subject: str = "") -> str:
    from ..campaigns.themes import get_theme

    if style.strip():
        # The brand's own look (Settings → voice → image_style), about the subject of this post.
        about = f" The scene suggests: {subject.strip()[:160]}." if subject.strip() else ""
        return (f"{style.strip().rstrip('.')}.{about} Calm, uncluttered centre so text can sit over it. "
                "No text, no lettering, no logos, no screens with readable words.")
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
    texts = options.get("texts") or {}
    prompt = background_prompt(
        options.get("theme_key", ""), products, post.get("angle", ""), style=sctx.settings.voice.image_style,
        subject=str(texts.get("topic") or texts.get("offering") or post.get("title") or ""),
    )
    tools = get_creative_tools(sctx, correlation_id=post["id"])
    used = "postiz_ai_image"
    try:
        generated = await tools.generate_image(prompt)
        path, _, _, _ = await _download(sctx, generated.url, post_id=post["id"], fmt="background", position=0,
                                        default_ext=".jpg")
    except SocialEngineError as exc:
        if not fallback_applies(exc) or not sctx.settings.postiz.image_fallback_model_id.strip():
            notes.warnings.append(f"Postiz AI background skipped: {exc.message}")
            return None
        try:
            path, used = await fallback_image(sctx, prompt=prompt, post_id=post["id"])
        except SocialEngineError as fallback_exc:
            notes.warnings.append(f"Postiz AI background skipped: {exc.message}; backup image model failed: "
                                  f"{fallback_exc.message}")
            return None
        notes.warnings.append(f"Postiz AI image unavailable ({exc.message}); used the backup image model instead.")
    finally:
        await tools.aclose()
    await post_store.update_post(sctx.store_id, post["id"], options={**options, "ai_background_path": path})
    notes.used.append(used)
    return path


def fallback_applies(exc: SocialEngineError) -> bool:
    """Postiz could not deliver the image: credits used up, or Postiz itself failed.

    A deliberate "budget is 0"/not-live configuration is not a reason to spend
    money elsewhere.
    """
    from ..errors import ConfigurationError, ValidationError

    return not isinstance(exc, (ConfigurationError, ValidationError))


async def fallback_image(sctx: StoreContext, *, prompt: str, post_id: str) -> tuple[str, str]:
    """Generate the image with the Models-page image model (e.g. Grok). Returns (path, source tag)."""
    import db
    from providers import ModelRecord, ProviderError, get_image_provider

    from ..errors import ConfigurationError, ExternalServiceError

    model_id = sctx.settings.postiz.image_fallback_model_id.strip()
    row = await db.get_model(model_id)
    if not row or str(row.get("store_id") or "") != sctx.store_id:
        raise ConfigurationError(f"Backup image model '{model_id}' does not exist for this store.")
    if row.get("model_type") != "image":
        raise ConfigurationError(f"Backup model '{row.get('name')}' is not an image model.")
    model_record = ModelRecord.from_dict(row)
    started = time.monotonic()
    try:
        urls = await get_image_provider(model_record).generate_images(prompt[:1500], count=1)
    except ProviderError as exc:
        raise ExternalServiceError("image_model", f"{model_record.name}: {exc}", retryable=exc.retryable) from exc
    except ValueError as exc:
        raise ConfigurationError(f"Backup image model '{model_record.name}': {exc}") from exc
    except Exception as exc:
        raise ExternalServiceError("image_model", f"{model_record.name}: {type(exc).__name__}: {exc}") from exc
    if not urls:
        raise ExternalServiceError("image_model", f"{model_record.name} returned no image", retryable=False)
    path, _, _, _ = await _download(sctx, urls[0], post_id=post_id, fmt="background", position=0, default_ext=".jpg")
    await record_api(sctx, model_record.name, started, post_id)
    await record(sctx.store_id, "post", post_id, f"AI background made with the backup image model ({model_record.name})",
                 stage="generate")
    return path, f"image_model:{model_record.name}"


async def record_api(sctx: StoreContext, name: str, started: float, post_id: str) -> None:
    try:
        await observability.record_api_call(
            service="image_model", operation="images.generate", method="POST", url_path=f"registry/{name}",
            status_code=200, ok=True, duration_ms=int((time.monotonic() - started) * 1000),
            store_id=sctx.store_id, correlation_id=post_id,
        )
    except Exception:
        logger.debug("Could not ledger the backup image call", exc_info=True)


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
