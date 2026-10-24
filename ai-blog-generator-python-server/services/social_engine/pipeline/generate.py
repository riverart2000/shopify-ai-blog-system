"""Generate a planned post: captions (Gemma) → creatives (templates) → targets
→ guards + AI review → approval decision.

Idempotent: re-running on a post that is already generated is a no-op unless
`force=True` (used by "Regenerate").
"""
from __future__ import annotations

import logging
import time
from typing import Any

from db.social_engine import config_store
from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ..activity import record
from ..context import StoreContext, load_store_context
from ..errors import ConfigurationError, NotFoundError, ValidationError
from ..jobs.queue import enqueue
from ..models import (
    CONTENT_TYPE_LABELS, MULTI_PRODUCT_CONTENT_TYPES, VIDEO_CONTENT_TYPES, ApprovalMode, ContentType,
    MediaFormat, profile_for,
)

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")

_REGENERATE_FROM = ("planned", "failed", "pending_approval", "approved", "generating")
_NEEDS_PRODUCTS = {
    ContentType.SINGLE_PRODUCT, ContentType.NEW_ARRIVAL, ContentType.SALE_PROMO, ContentType.BACK_IN_STOCK,
    ContentType.PRODUCT_OF_THE_DAY, ContentType.COUNTDOWN, ContentType.PRODUCT_VIDEO, ContentType.AI_IMAGE_AD,
    *MULTI_PRODUCT_CONTENT_TYPES,
}


def product_snapshot(products: list[dict]) -> dict[str, dict]:
    """What the creative/caption showed, so stale posts can be detected later."""
    return {
        str(p["product_id"]): {
            "price_min": round(float(p.get("price_min") or 0), 2),
            "compare_at_max": round(float(p["compare_at_max"]), 2) if p.get("compare_at_max") else None,
            "on_sale": bool(p.get("on_sale")),
            "available": bool(p.get("available")),
            "title": p.get("title", ""),
        }
        for p in products
    }


def _channel_kind(channel: dict) -> str:
    settings = channel.get("default_settings") or {}
    if not settings.get("post_type") and settings.get("story") in (True, "true", "yes", "1", 1):
        return "story"
    return str(settings.get("post_type") or settings.get("kind") or "post")


def _format_for(channel: dict, content_type: str) -> tuple[str, str] | None:
    """(media format, kind) a channel needs for this post, or None if it can't carry it.

    For video posts, channels that only take images (e.g. Google Business) get a
    branded still image instead of being skipped.
    """
    profile = profile_for(channel.get("provider", ""))
    if content_type in VIDEO_CONTENT_TYPES and profile.supports_video:
        return profile.video_format, "video"
    if profile.video_only or not profile.image_formats:
        return None
    if _channel_kind(channel) in {"story", "reel"}:
        return MediaFormat.STORY, "image"
    return profile.image_formats[0], "image"


def _platform_settings(channel: dict, content_type: str, texts: dict, link: str) -> dict[str, Any]:
    """Per-network defaults derived from the post (e.g. Google Business offer posts)."""
    if channel.get("provider") == "gmb":
        settings: dict[str, Any] = {"callToActionType": "SHOP", "callToActionUrl": link} if link else {}
        if content_type in (ContentType.SALE_PROMO, ContentType.COUNTDOWN):
            settings["topicType"] = "OFFER"
            if texts.get("coupon_code"):
                settings["offerCouponCode"] = texts["coupon_code"]
            if link:
                settings["offerRedeemUrl"] = link
        return settings
    return {}


def _render_product(product: dict, sctx: StoreContext, *, image_offset: int):
    from ..catalog import format_price
    from ..creatives.base import RenderProduct

    brand = sctx.settings.brand
    price = format_price(product.get("price_min") or 0, brand.currency_symbol, brand.include_taxes_pct)
    compare = ""
    if product.get("on_sale") and product.get("compare_at_max"):
        compare = format_price(product["compare_at_max"], brand.currency_symbol, brand.include_taxes_pct)
    images = [img.get("url") for img in product.get("images") or [] if img.get("url")]
    if not images and product.get("featured_image_url"):
        images = [product["featured_image_url"]]
    return RenderProduct(
        product_id=str(product["product_id"]), title=product.get("title", ""), handle=product.get("handle", ""),
        url=product.get("url") or sctx.product_url(product.get("handle", "")), price=price,
        price_value=float(product.get("price_min") or 0), compare_at=compare,
        discount_pct=float(product.get("discount_pct") or 0), image_urls=images,
        description=(product.get("description") or "")[:600], tags=list(product.get("tags") or []),
        collections=[c.get("title", "") for c in product.get("collections") or []],
    )


def _base_link(sctx: StoreContext, post: dict, products: list[dict]) -> str:
    options = post.get("options") or {}
    if options.get("link_override"):
        return str(options["link_override"])
    if len(products) == 1:
        return products[0].get("url") or sctx.product_url(products[0].get("handle", ""))
    if options.get("collection_handle"):
        return f"{sctx.storefront_url}/collections/{options['collection_handle']}"
    return sctx.storefront_url


async def _brand_content_texts(sctx: StoreContext, post: dict, texts: dict) -> dict:
    """Ask Gemma for a quote/fact/poll/quiz when a brand-content post has none yet."""
    kind = texts.get("brand_kind")
    if not kind or texts.get("quote") or texts.get("body"):
        return texts
    from ..ai.decisions import generate_brand_content

    recent_rows = await post_store.list_posts(sctx.store_id, content_type=ContentType.QUOTE, order="newest", limit=15)
    recent = [r.get("base_caption", "")[:160] for r in recent_rows if r.get("base_caption")]
    content = await generate_brand_content(sctx, kind=kind, topic_hint=texts.get("topic_hint", ""), recent=recent)
    data = content.model_dump() if hasattr(content, "model_dump") else dict(content)
    merged = dict(texts)
    merged.update({
        "headline": data.get("headline", ""),
        "quote": data.get("body", ""),
        "body": data.get("body", ""),
        "poll_options": " | ".join(data.get("options") or []),
        "answer": data.get("answer", "") or "",
        "brand_caption": data.get("caption", ""),
    })
    return merged


async def generate_post(store_id: str, post_id: str, *, force: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    post = await post_store.get_post(store_id, post_id)
    if not post:
        raise NotFoundError(f"Post {post_id} was not found.")
    if post["status"] not in _REGENERATE_FROM:
        return {"skipped": True, "reason": f"status is {post['status']}"}
    if post["status"] in ("pending_approval", "approved") and not force:
        return {"skipped": True, "reason": "already generated"}
    if not await post_store.transition_post(store_id, post_id, from_statuses=_REGENERATE_FROM,
                                            to_status="generating", error_type="", error_message=""):
        return {"skipped": True, "reason": "status changed concurrently"}
    await record(store_id, "post", post_id, "Generating captions and creatives", stage="generate")

    sctx = await load_store_context(store_id, correlation_id=post_id)
    settings = sctx.settings
    options = dict(post.get("options") or {})
    content_type = post["content_type"]

    # Channels -------------------------------------------------------------
    channels: list[dict] = []
    per_post_settings = options.get("channel_settings") or {}
    for channel_id in options.get("channel_ids") or []:
        channel = await config_store.get_channel(store_id, str(channel_id))
        if channel and channel.get("enabled") and not channel.get("disabled") and not channel.get("missing_at"):
            # This post's own choices (e.g. "story" in Studio) override the channel
            # defaults, so the format and caption style follow them too.
            override = per_post_settings.get(str(channel_id)) or {}
            if override:
                channel = {**channel, "default_settings": {**(channel.get("default_settings") or {}), **override}}
            channels.append(channel)
    if not channels:
        raise ConfigurationError("None of this post's channels are connected and enabled any more.")

    # Products -------------------------------------------------------------
    products = await product_store.get_products(store_id, post.get("product_ids") or [])
    gone = [p["title"] for p in products if p.get("deleted_at") or not p.get("available")]
    products = [p for p in products if not p.get("deleted_at") and p.get("available")]
    if content_type in _NEEDS_PRODUCTS and not products:
        detail = f" ({', '.join(gone)})" if gone else ""
        raise ValidationError(f"The product(s) for this post are no longer available{detail}.")
    if content_type == ContentType.SALE_PROMO and products and not any(p.get("on_sale") for p in products) \
            and not (options.get("texts") or {}).get("promo_text"):
        raise ValidationError(f"'{products[0]['title']}' is no longer on sale.")

    texts = await _brand_content_texts(sctx, post, dict(options.get("texts") or {}))

    # Postiz creative sources (AI video / library designs) ---------------------
    from .media_sources import SourceNotes, ai_background, ai_video, postiz_design, tiktok_music

    notes = SourceNotes()
    render_content_type = content_type
    premade: list[dict] = []          # media that already lives in Postiz (no rendering, no re-upload)
    if content_type == ContentType.AI_VIDEO:
        video = await ai_video(sctx, post=post, products=products, notes=notes)  # may raise Deferred
        if video:
            premade.append(video)
        else:
            render_content_type = ContentType.PRODUCT_VIDEO
    design = None
    if options.get("postiz_media_search") and content_type not in VIDEO_CONTENT_TYPES:
        design = await postiz_design(sctx, post=post, search=str(options["postiz_media_search"]), notes=notes)
    if content_type == ContentType.AI_IMAGE_AD:
        render_content_type = ContentType.SALE_PROMO if any(p.get("on_sale") for p in products) \
            else ContentType.SINGLE_PRODUCT
    background_path = None
    wants_background = content_type == ContentType.AI_IMAGE_AD or bool(options.get("ai_background"))
    if wants_background and not design and render_content_type not in VIDEO_CONTENT_TYPES:
        from ..publishers.guard import live_publishing_enabled

        if live_publishing_enabled():
            background_path = await ai_background(sctx, post=post, products=products, notes=notes)
        else:
            notes.warnings.append("Postiz AI backgrounds are only generated on the live system.")

    # Custom caption (Outfy "write my own caption") → template captions with placeholders.
    if options.get("custom_caption"):
        options["caption_overrides"] = {
            **(options.get("caption_overrides") or {}),
            "source": "template",
            "templates": {"default": str(options["custom_caption"])},
        }

    # Links + captions -----------------------------------------------------
    from ..ai.base import CaptionChannel, CaptionRequest
    from ..ai.captions import generate_captions, template_captions
    from ..utm import tracked_url

    campaign = await config_store.get_campaign(store_id, post["campaign_id"]) if post.get("campaign_id") else None
    utm_campaign = (campaign or {}).get("name") or post.get("rule_type") or "manual"
    base_link = _base_link(sctx, post, products)
    usable: list[tuple[dict, str, str]] = []
    skipped: list[str] = []
    for channel in channels:
        plan = _format_for(channel, content_type)
        if plan is None:
            skipped.append(f"{channel.get('name') or channel['channel_id']} ({channel['provider']} can't post "
                           f"{CONTENT_TYPE_LABELS.get(content_type, content_type)})")
            continue
        usable.append((channel, plan[0], plan[1]))
    if not usable:
        raise ValidationError("No selected channel can publish this content type: " + "; ".join(skipped))

    caption_channels = [
        CaptionChannel(
            channel_id=str(channel["channel_id"]), provider=channel["provider"],
            max_length=channel.get("max_length"), rules=channel.get("rules") or "",
            link_url=tracked_url(base_link, provider=channel["provider"], campaign=utm_campaign,
                                 content=post_id, settings=settings.utm),
            kind=_channel_kind(channel),
        )
        for channel, _, _ in usable
    ]
    caption_request = CaptionRequest(
        store_id=store_id, content_type=content_type, products=products, channels=caption_channels,
        angle=post.get("angle", ""), brief=post.get("brief", "") or texts.get("brand_caption", ""),
        texts=texts, caption_overrides=options.get("caption_overrides") or {}, correlation_id=post_id,
    )
    overrides = options.get("caption_overrides") or {}
    source = overrides.get("source") or settings.captions.source
    captions = template_captions(caption_request, sctx) if source == "template" \
        else await generate_captions(caption_request, sctx)

    # Creatives ------------------------------------------------------------
    from ..creatives import TemplateRenderer, load_store_logo, templates_for
    from ..creatives.base import RenderRequest
    from ..creatives.storage import delete_files

    image_offset = int(options.get("image_offset") or 0)
    is_video_post = content_type in VIDEO_CONTENT_TYPES
    formats = sorted({fmt for _, fmt, kind in usable if kind == ("video" if is_video_post else "image")})
    still_formats = sorted({fmt for _, fmt, kind in usable if kind == "image"}) if is_video_post else []
    if premade or design:
        formats = [] if premade else formats   # Postiz video replaces our video render
    if design:
        formats = []                           # the Postiz design is used for every image channel
    template_key = post.get("template_key")
    if not template_key or content_type in (ContentType.AI_IMAGE_AD, ContentType.AI_VIDEO):
        template_key = next(iter(templates_for(render_content_type)), "")
    theme_texts: dict[str, str] = {}
    if options.get("theme_key"):
        from ..campaigns.themes import get_theme

        # A Studio post that picks a theme gets the theme's occasion and sticker,
        # like a promotion does; anything the merchant typed still wins.
        theme = get_theme(str(options["theme_key"]))
        if theme.category == "occasion":
            theme_texts["occasion"] = theme.label
        if theme.sticker:
            theme_texts["sticker"] = theme.sticker
    texts = {**theme_texts, **texts}
    render_texts = {
        **texts,
        "headline": texts.get("headline") or captions.headline,
        "subheadline": texts.get("subheadline") or captions.subheadline,
        "cta": captions.cta or settings.brand.cta_text,
        "store_name": settings.brand.business_name,
        "website": sctx.storefront_url.replace("https://", "").replace("http://", ""),
    }
    brand = settings.brand
    if options.get("theme_key"):
        from ..campaigns.themes import theme_palette
        from ..settings import BrandColors

        palette = theme_palette(str(options["theme_key"]))
        if palette:
            brand = brand.model_copy(update={"colors": BrandColors(**{**brand.colors.model_dump(), **palette})})
    show_logo = brand.use_store_logo and brand.logo_position != "none" and options.get("show_logo", True)
    render_options: dict[str, Any] = {
        "show_price": brand.show_price and (overrides.get("include_price", settings.captions.include_price)),
        "show_logo": show_logo,
        "carousel": content_type == ContentType.CAROUSEL,
        "max_images": 10 if settings.content.multi_image else 1,
        "music_path": brand.music_path,
    }
    if texts.get("sticker"):
        render_options["sticker"] = texts["sticker"]
    if background_path:
        render_options["background_image"] = background_path
    render_request = RenderRequest(
        store_id=store_id, post_id=post_id, content_type=render_content_type, template_key=template_key,
        formats=formats,
        products=[_render_product(p, sctx, image_offset=image_offset) for p in products],
        texts={k: str(v) for k, v in render_texts.items() if v is not None},
        brand=brand,
        logo_png=await load_store_logo(store_id) if show_logo else None,
        image_offset=image_offset,
        options=render_options,
    )
    renderer = TemplateRenderer()
    rendered = await renderer.render(render_request) if formats else []
    if still_formats and not design:
        # Image-only channels on a video post get the matching branded still.
        from dataclasses import replace

        rendered += await renderer.render(replace(
            render_request, content_type=ContentType.SINGLE_PRODUCT, formats=still_formats,
            template_key=next(iter(templates_for(ContentType.SINGLE_PRODUCT)), ""),
        ))
    if not rendered and not premade and not design:
        raise ValidationError(f"Template '{template_key}' produced no media.")

    keep = {options.get("ai_background_path")} - {None}
    old_assets = await post_store.detach_assets(post_id)
    await delete_files([a["path"] for a in old_assets if a["path"] not in keep])
    assets: dict[tuple[str, str], list[dict]] = {}
    thumbnails: dict[str, str] = {}
    for media in rendered:
        asset_id = await post_store.add_asset(store_id, {
            "post_id": post_id, "kind": media.kind, "format": media.format, "role": media.role,
            "position": media.position, "path": media.path, "mime": media.mime, "width": media.width,
            "height": media.height, "duration_ms": media.duration_ms, "bytes": media.bytes,
            "sha256": media.sha256, "source": media.source,
        })
        if media.role == "thumbnail":
            thumbnails.setdefault(media.format, asset_id)
        else:
            assets.setdefault((media.format, media.kind), []).append({"id": asset_id, "position": media.position})
    for item in premade:   # Postiz AI video: already in the Postiz library
        asset_id = await post_store.add_asset(store_id, {
            "post_id": post_id, "kind": "video", "format": item["format"], "role": "primary", "position": 0,
            "path": item["path"], "mime": item["mime"], "width": item["width"], "height": item["height"],
            "duration_ms": item["duration_ms"], "bytes": item["bytes"], "sha256": item["sha256"],
            "source": item["source"],
        })
        if item.get("external_id"):
            await post_store.set_asset_uploaded(asset_id, external_id=item["external_id"],
                                                external_path=item["external_path"])
        assets.setdefault((item["format"], "video"), []).append({"id": asset_id, "position": 0})
    design_asset_id = ""
    if design:
        design_asset_id = await post_store.add_asset(store_id, {
            "post_id": post_id, "kind": "image", "format": "design", "role": "primary", "position": 0,
            "path": design["path"], "mime": design["mime"], "bytes": design["bytes"], "sha256": design["sha256"],
            "source": f"postiz_design:{design.get('name', '')}",
        })
        if design.get("external_id"):
            await post_store.set_asset_uploaded(design_asset_id, external_id=design["external_id"],
                                                external_path=design["external_path"])

    # Targets --------------------------------------------------------------
    channel_times = {str(k): int(v) for k, v in (options.get("channel_times") or {}).items()}
    targets: list[dict] = []
    for channel, fmt, kind in usable:
        profile = profile_for(channel["provider"])
        media = sorted(assets.get((fmt, kind), []), key=lambda a: a["position"])
        # A story takes exactly one image or video, so a carousel sends its first slide.
        limit = max(1, profile.max_images) if kind == "image" and _channel_kind(channel) != "story" else 1
        media_ids = [a["id"] for a in media][:limit]
        if kind == "image" and design_asset_id:
            media_ids = [design_asset_id]
        if kind == "video" and media_ids and channel["provider"] == "pinterest" and thumbnails.get(fmt):
            media_ids.append(thumbnails[fmt])  # Pinterest video pins need a cover image
        if not media_ids and profile.media == "required":
            skipped.append(f"{channel.get('name') or channel['channel_id']} (no {fmt} {kind} rendered)")
            continue
        caption = captions.channel_captions.get(str(channel["channel_id"]))
        link = next((c.link_url for c in caption_channels if c.channel_id == str(channel["channel_id"])), "")
        target_settings = {**_platform_settings(channel, content_type, texts, link),
                           **(channel.get("default_settings") or {}),
                           **((options.get("channel_settings") or {}).get(str(channel["channel_id"])) or {})}
        if channel["provider"] == "tiktok-business" and settings.postiz.trending_tiktok_music:
            if kind == "video" and not target_settings.get("music"):
                track = await tiktok_music(sctx, str(channel["channel_id"]), correlation_id=post_id)
                if track:
                    target_settings["music"] = track
                    notes.used.append("tiktok_music")
            elif kind == "image":
                target_settings.setdefault("autoAddMusic", "yes")
        targets.append({
            "channel_id": str(channel["channel_id"]),
            "provider": channel["provider"],
            "caption": caption.caption if caption else captions.base_caption,
            "first_comment": caption.first_comment if caption else "",
            "title": caption.title if caption else post.get("title", ""),
            "settings": target_settings,
            "media_ids": media_ids,
            "format": fmt,
            "link_url": link,
            "scheduled_at": channel_times.get(str(channel["channel_id"])) or post.get("scheduled_at"),
        })
    if not targets:
        raise ValidationError("Nothing to publish: " + "; ".join(skipped))
    await post_store.replace_targets(store_id, post_id, targets)

    # Guards + review ------------------------------------------------------
    from ..ai.guard import check_captions

    guard = check_captions(captions, settings=settings)
    review_data: dict[str, Any] = {
        "guard": {"hold": guard.hold, "issues": [vars(issue) for issue in guard.issues]},
    }
    approval_mode = post.get("approval_mode") or settings.default_approval_mode
    review = None
    if settings.ai.ai_review or approval_mode == ApprovalMode.AI_REVIEW:
        from ..ai.review import review_post

        try:
            review = await review_post(
                sctx, post={**post, "options": options}, captions=captions, products=products,
                media_paths=[m.path for m in rendered if m.kind == "image"][:1],
            )
            review_data["ai_review"] = vars(review)
        except Exception as exc:  # review is advisory; a failure must not lose the post
            review_data["ai_review"] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.warning("AI review failed for %s: %s", post_id, exc,
                           extra={"store_id": store_id, "correlation_id": post_id})

    if guard.hold:
        status = "pending_approval"
        reason = "held by content guard"
    elif approval_mode == ApprovalMode.AUTO:
        status, reason = "approved", "auto-approved"
    elif approval_mode == ApprovalMode.AI_REVIEW and review is not None and review.approve:
        status, reason = "approved", f"approved by Gemma review (score {review.score}/10)"
    else:
        status, reason = "pending_approval", "waiting for approval"

    generation = {
        "caption_source": captions.source,
        "model": captions.model,
        "used_fallback": captions.used_fallback,
        "warnings": list(captions.warnings) + notes.warnings + [f"Skipped {s}" for s in skipped],
        "postiz_tools": notes.used,
        "rendered_as": render_content_type,
        "template_key": template_key,
        "formats": formats,
        "media": [{"format": m.format, "kind": m.kind, "role": m.role, "source": m.source} for m in rendered],
        "hook": captions.hook,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "generated_at": int(time.time()),
        "product_snapshot": product_snapshot(products),
    }
    updated = await post_store.transition_post(
        store_id, post_id, from_statuses=("generating",), to_status=status,
        base_caption=captions.base_caption, hashtags=captions.hashtags, alt_text=captions.alt_text,
        link_url=base_link, template_key=template_key, generation=generation, review=review_data,
        error_type="", error_message="",
        approved_at=int(time.time()) if status == "approved" else None,
        approved_by="auto" if status == "approved" else "",
    )
    if not updated:
        return {"skipped": True, "reason": "post was changed while generating"}
    await record(
        store_id, "post", post_id,
        f"Generated {len(targets)} channel version(s); {reason}"
        + (f"; skipped {len(skipped)} channel(s)" if skipped else ""),
        level="warning" if guard.hold or skipped else "info", stage="generate",
        data={"skipped": skipped, "guard_issues": len(guard.issues), "model": captions.model},
    )
    if status == "approved":
        await enqueue("post.publish", {"post_id": post_id}, store_id=store_id,
                      dedupe_key=f"publish:{post_id}", correlation_id=post_id)
    return {"status": status, "targets": len(targets), "skipped": skipped, "reason": reason}
