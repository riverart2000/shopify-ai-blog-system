"""Logic shared by the Postiz and dry-run publishers.

The dry-run publisher builds exactly the same create-post payload as the real
one, so a store tried end-to-end in dry-run mode hits the same validation
errors (missing Pinterest board, un-uploaded media...) it would hit live.
"""
from __future__ import annotations

import html
import mimetypes
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from db.social_engine import observability

from ..errors import ConfigurationError, RateLimited, ValidationError
from ..models import profile_for
from .base import ExternalPost, OutboundMedia, OutboundPost
from .postiz_client import CreatePostRequest, PostContent, PostItem, iso_utc
from .postiz_settings import build_settings, clean_text, network_name

IMAGE_MAX_BYTES = 10 * 1024 * 1024          # Postiz Cloud: images ≤ 10 MB
VIDEO_MAX_BYTES = 1024 * 1024 * 1024        # Postiz Cloud: video ≤ 1 GB
CAPTION_MATCH_CHARS = 80
CREATE_OPERATION = "posts.create"
VIDEO_OPERATION = "video.generate"
CREATE_WINDOW_SECONDS = 3600
VIDEO_WINDOW_SECONDS = 30 * 86400
PUBLISH_MODES = ("schedule", "now", "draft")

_HTML_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: Any, what: str = "datetime") -> datetime:
    """Require a timezone-aware datetime (a naive one would post at the wrong time)."""
    if not isinstance(value, datetime):
        raise ValidationError(f"{what} must be a datetime, got {type(value).__name__}.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{what} must be timezone-aware UTC, got naive {value.isoformat()}.")
    return value.astimezone(UTC)


def parse_datetime(value: Any) -> datetime | None:
    """Parse Postiz dates ("…Z", "…+00:00" or naive = UTC) and epoch numbers."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), UTC)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

def media_is_video(media: OutboundMedia) -> bool:
    return media.kind == "video" or (media.mime or "").lower().startswith("video/")


def check_media_file(media: OutboundMedia) -> tuple[Path, int]:
    """Validate a local file against the Postiz upload limits. Returns (path, size)."""
    path = Path(media.path or "")
    if not media.path or not path.is_file():
        raise ValidationError(
            f"Media file for asset {media.asset_id} does not exist: {media.path!r}.",
            details={"asset_id": media.asset_id, "path": media.path},
        )
    size = path.stat().st_size
    if size <= 0:
        raise ValidationError(f"Media file for asset {media.asset_id} is empty: {media.path}.",
                              details={"asset_id": media.asset_id})
    video = media_is_video(media)
    limit = VIDEO_MAX_BYTES if video else IMAGE_MAX_BYTES
    if size > limit:
        raise ValidationError(
            f"{'Video' if video else 'Image'} for asset {media.asset_id} is {size / 1048576:.1f} MB; "
            f"Postiz accepts at most {limit // 1048576} MB.",
            details={"asset_id": media.asset_id, "bytes": size, "limit": limit},
        )
    return path, size


def upload_filename(path: Path, mime: str) -> tuple[str, str]:
    """Filename with a real extension + the MIME type to declare for it."""
    guessed = mimetypes.guess_type(path.name)[0] or ""
    content_type = (mime or guessed or "application/octet-stream").lower()
    name = path.name or "upload"
    if not path.suffix:
        extension = mimetypes.guess_extension(content_type) or ""
        name = f"{name}{extension}"
    return name, content_type


# ---------------------------------------------------------------------------
# Create-post payload
# ---------------------------------------------------------------------------

def target_group(post: OutboundPost, target_id: str) -> str:
    """Postiz `group` for one post item.

    Postiz deletes by group (DELETE /posts/{id} removes every post sharing the
    id's group) and re-uses a group when it sees it again, so each target gets
    its own group derived from ours. Cancelling or deleting one target can
    then never touch its siblings, and targets of one post may be created in
    several requests (different times) without interfering.
    """
    base = clean_text(post.group) or clean_text(post.post_id) or "post"
    return f"{base}-{target_id}"


@dataclass
class CreatePlan:
    body: CreatePostRequest
    groups: dict[str, str]          # target_id → Postiz group
    date: datetime


def _tags(values: Iterable[str]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        name = clean_text(value).lstrip("#")
        if name and name.lower() not in seen:
            seen.add(name.lower())
            tags.append({"value": name, "label": name})
    return tags


def build_create_payload(
    post: OutboundPost,
    *,
    short_links: bool,
    tag_posts: bool,
    now: datetime,
    channel_labels: dict[str, str] | None = None,
) -> CreatePlan:
    """Validate an `OutboundPost` and build the single CreatePostRequest for it."""
    if post.mode not in PUBLISH_MODES:
        raise ValidationError(f"Unknown publish mode {post.mode!r} for post {post.post_id}; use one of {PUBLISH_MODES}.")
    if not post.targets:
        raise ValidationError(f"Post {post.post_id} has no targets to publish.")

    times = {ensure_utc(t.scheduled_at, f"scheduled_at of target {t.target_id}") for t in post.targets}
    if len(times) > 1:
        listed = ", ".join(sorted(iso_utc(t) for t in times))
        raise ValidationError(
            f"All targets in one Postiz create request must share scheduled_at; post {post.post_id} has "
            f"{len(times)} different times ({listed}). Split the targets by scheduled_at.",
            details={"post_id": post.post_id, "times": sorted(iso_utc(t) for t in times)},
        )
    channels = [t.channel_id for t in post.targets]
    duplicates = sorted({c for c in channels if channels.count(c) > 1})
    if duplicates:
        raise ValidationError(
            f"Post {post.post_id} targets channel(s) {', '.join(duplicates)} more than once; "
            "one create request carries one post per channel.",
            details={"post_id": post.post_id, "channels": duplicates},
        )

    labels = channel_labels or {}
    items: list[PostItem] = []
    groups: dict[str, str] = {}
    for target in post.targets:
        provider = (target.provider or "").strip().lower()
        profile = profile_for(provider)
        network = network_name(provider)
        label = labels.get(target.channel_id) or clean_text(target.settings.get("channel_name")) or target.channel_id
        where = f"{network} channel {label} (target {target.target_id})"
        if not target.channel_id:
            raise ValidationError(f"Target {target.target_id} has no channel id.")

        images: list[dict[str, str]] = []
        image_count = video_count = 0
        for media in target.media:
            if not media.external_id or not media.external_path:
                raise ValidationError(
                    f"Media {media.asset_id} for {where} has not been uploaded to Postiz yet "
                    "(no external id/path). Upload it before creating the post.",
                    details={"target_id": target.target_id, "asset_id": media.asset_id},
                )
            images.append({"id": media.external_id, "path": media.external_path})
            if media_is_video(media):
                video_count += 1
            else:
                image_count += 1
        if profile.media == "required" and not images:
            raise ValidationError(f"{where} requires an image or video; none attached.",
                                  details={"target_id": target.target_id})
        if profile.media == "none" and images:
            raise ValidationError(f"{where} does not accept media.", details={"target_id": target.target_id})
        if profile.video_only and not video_count:
            raise ValidationError(f"{where} only accepts video; attach a video.",
                                  details={"target_id": target.target_id})
        if video_count and not profile.supports_video:
            raise ValidationError(f"{where} does not accept video.", details={"target_id": target.target_id})
        if image_count > profile.max_images:
            raise ValidationError(
                f"{where} accepts at most {profile.max_images} image(s); {image_count} attached.",
                details={"target_id": target.target_id},
            )
        caption = target.caption or ""
        if not caption.strip() and not images:
            raise ValidationError(f"{where} has neither a caption nor media.", details={"target_id": target.target_id})

        comments = [c for c in target.comments if c and c.strip()]
        if comments and not profile.supports_comments:
            raise ValidationError(
                f"{network} does not support comments or thread replies; remove the extra part(s) "
                f"from {where}.",
                details={"target_id": target.target_id, "provider": provider},
            )

        settings = build_settings(
            provider, target.settings, title=target.title, link_url=target.link_url,
            image_count=image_count, video_count=video_count, channel_label=label,
        )
        parts: list[PostContent] = [{"content": caption, "image": images}]
        # Comments / thread replies are text-only parts: this also satisfies the
        # networks whose comments cannot carry media (Instagram, LinkedIn...).
        parts.extend({"content": comment, "image": []} for comment in comments)
        group = target_group(post, target.target_id)
        groups[target.target_id] = group
        items.append({"integration": {"id": target.channel_id}, "value": parts, "group": group, "settings": settings})

    scheduled = next(iter(times))
    date = ensure_utc(now, "now") if post.mode == "now" else scheduled
    body: CreatePostRequest = {
        "type": post.mode,
        "date": iso_utc(date),
        "shortLink": bool(short_links),
        "tags": _tags(post.tags) if tag_posts else [],
        "posts": items,
    }
    return CreatePlan(body=body, groups=groups, date=date)


# ---------------------------------------------------------------------------
# Reconciliation: find the post an ambiguous create call may have produced
# ---------------------------------------------------------------------------

def normalise_caption(text: Any) -> str:
    """Comparable caption text: no HTML, no URLs (short links rewrite them), collapsed whitespace."""
    value = html.unescape(_HTML_TAG.sub(" ", str(text or "")))
    value = _URL.sub(" ", value)
    return " ".join(value.split()).casefold()


def caption_matches(expected: str, actual: str, *, chars: int = CAPTION_MATCH_CHARS) -> bool:
    a = normalise_caption(expected)[:chars]
    b = normalise_caption(actual)[:chars]
    if not a or not b:
        return a == b
    shorter, longer = sorted((a, b), key=len)
    return longer.startswith(shorter)


def pick_existing(
    posts: Iterable[ExternalPost], channel_id: str, scheduled_at: datetime, caption: str,
) -> ExternalPost | None:
    """Best match on channel + caption prefix, closest to `scheduled_at`."""
    candidates = [p for p in posts if p.channel_id == channel_id and caption_matches(caption, p.content)]
    if not candidates:
        return None

    def distance(post: ExternalPost) -> float:
        return abs((post.publish_date - scheduled_at).total_seconds()) if post.publish_date else float("inf")

    return min(candidates, key=distance)


# ---------------------------------------------------------------------------
# Budgets (counted from the API ledger, so they hold across processes)
# ---------------------------------------------------------------------------

async def window_usage(service: str, operation: str, window_seconds: int) -> tuple[int, int | None]:
    since = int(time.time()) - window_seconds
    used = await observability.count_api_calls(service=service, operation=operation, since=since)
    oldest = await observability.oldest_api_call_since(service=service, operation=operation, since=since) if used else None
    return used, oldest


async def enforce_video_budget(service: str, monthly_budget: int) -> None:
    if monthly_budget <= 0:
        raise ConfigurationError(
            "AI video is disabled: the Postiz AI video monthly budget is 0. "
            "Raise it in Social Engine settings → Postiz to generate AI videos.",
            details={"budget": monthly_budget},
        )
    used, oldest = await window_usage(service, VIDEO_OPERATION, VIDEO_WINDOW_SECONDS)
    if used >= monthly_budget:
        retry_at = (oldest or int(time.time())) + VIDEO_WINDOW_SECONDS + 5
        raise RateLimited(
            f"AI video budget reached: {used}/{monthly_budget} videos generated in the last 30 days. "
            f"The next one can be generated after {iso_utc(datetime.fromtimestamp(retry_at, UTC))}.",
            retry_at=retry_at,
            details={"used": used, "budget": monthly_budget},
        )
