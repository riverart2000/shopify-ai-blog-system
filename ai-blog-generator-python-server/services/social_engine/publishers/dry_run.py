"""Dry-run publisher: same contract as Postiz, sends nothing.

* Validates and builds the exact Postiz create payload (so configuration
  errors surface before Postiz is connected) and records every call in the
  API ledger under service "dry_run".
* Created posts are kept in engine state (`dry_run:posts:{store_id}`), so
  `list_posts` / `find_existing_post` work across processes.
* A queued post reports PUBLISHED (with a fake release URL) once its time has
  passed, so the reconcile path can be exercised end-to-end.
* `list_channels` returns a fixed set of fake channels with stable ids.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Literal

from db.social_engine import observability

from ..context import StoreContext
from ..errors import NotFoundError, ValidationError
from ..http import CallContext
from ..models import profile_for
from .base import (
    ChannelRules, ExternalPost, OutboundMedia, OutboundPost, PostMetrics, PublishedTarget,
    PublisherChannel, PublisherNotification, UploadedMedia,
)
from .common import (
    VIDEO_OPERATION, build_create_payload, check_media_file, enforce_video_budget, ensure_utc,
    parse_datetime, pick_existing, upload_filename,
)
from .postiz import CANONICAL_METRICS, VIDEO_KINDS, VIDEO_ORIENTATIONS
from .postiz_client import iso_utc

logger = logging.getLogger("ai_blog_server.social_engine.publishers")

SERVICE = "dry_run"
MAX_STORED_POSTS = 1000
RELEASE_URL_BASE = "https://dry-run.invalid"

DRY_RUN_CHANNELS: tuple[PublisherChannel, ...] = (
    PublisherChannel("dry-instagram", "instagram", "Instagram (dry run)", "dry.run.shop"),
    PublisherChannel("dry-facebook", "facebook", "Facebook Page (dry run)", "dryrunshop"),
    PublisherChannel("dry-pinterest", "pinterest", "Pinterest (dry run)", "dryrunshop"),
    PublisherChannel("dry-tiktok", "tiktok", "TikTok (dry run)", "dryrunshop"),
    PublisherChannel("dry-x", "x", "X (dry run)", "dryrunshop"),
)


class DryRunPublisher:
    name = "dry_run"

    def __init__(
        self,
        sctx: StoreContext,
        ctx: CallContext | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sctx = sctx
        self.settings = sctx.settings.postiz
        self.ctx = ctx or sctx.call_context()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def state_key(self) -> str:
        return f"dry_run:posts:{self.sctx.store_id}"

    async def __aenter__(self) -> "DryRunPublisher":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    def _now(self) -> datetime:
        return ensure_utc(self._clock(), "clock")

    async def _record(self, operation: str, method: str, request: Any = "", response: Any = "") -> None:
        try:
            await observability.record_api_call(
                service=SERVICE, operation=operation, method=method, url_path=f"/dry-run/{operation}",
                status_code=200, ok=True, duration_ms=0, attempt=1,
                store_id=self.ctx.store_id, correlation_id=self.ctx.correlation_id,
                request_excerpt=request, response_excerpt=response,
            )
        except Exception:  # the ledger must never break the caller
            logger.debug("Could not record dry-run call", exc_info=True)

    async def _load(self) -> list[dict[str, Any]]:
        posts = await observability.get_state(self.state_key, [])
        return [post for post in posts if isinstance(post, dict)] if isinstance(posts, list) else []

    async def _save(self, posts: list[dict[str, Any]]) -> None:
        await observability.set_state(self.state_key, posts[-MAX_STORED_POSTS:])

    def _to_external(self, record: dict[str, Any], now: datetime) -> ExternalPost:
        publish_date = parse_datetime(record.get("publish_date"))
        state = str(record.get("state") or "QUEUE")
        release_url = ""
        if state == "QUEUE" and publish_date is not None and publish_date <= now:
            state = "PUBLISHED"
        if state == "PUBLISHED":
            release_url = f"{RELEASE_URL_BASE}/{record.get('provider') or 'post'}/{record['id']}"
        return ExternalPost(
            external_id=str(record["id"]), channel_id=str(record.get("channel_id") or ""),
            provider=str(record.get("provider") or ""), content=str(record.get("content") or ""),
            publish_date=publish_date, state=state, release_url=release_url, group=str(record.get("group") or ""),
        )

    # -- account & channels ---------------------------------------------------

    async def check_connection(self) -> dict[str, Any]:
        result = {
            "connected": True, "publisher": self.name,
            "organization": {"id": "dry-run", "name": "Dry run — nothing is sent"}, "user": None,
        }
        await self._record("auth.is_connected", "GET", response=result)
        return result

    async def list_channels(self) -> list[PublisherChannel]:
        channels = [replace(channel) for channel in DRY_RUN_CHANNELS]
        await self._record("integrations.list", "GET", response=[c.channel_id for c in channels])
        return channels

    async def channel_rules(self, channel_id: str) -> ChannelRules:
        provider = next((c.provider for c in DRY_RUN_CHANNELS if c.channel_id == channel_id), "")
        profile = profile_for(provider)
        await self._record("integrations.settings", "GET", request={"channel_id": channel_id})
        return ChannelRules(
            max_length=profile.max_length,
            rules=f"Dry run: rules come from the local {profile.label} profile.",
            settings_schema=None,
            tools=[],
        )

    async def run_channel_tool(self, channel_id: str, method: str, data: dict[str, Any]) -> Any:
        await self._record("integrations.trigger", "POST", request={"channel_id": channel_id, "method": method, "data": data})
        if method == "boards":  # so Pinterest works end-to-end in rehearsals
            return [{"id": "dry-board", "name": "Dry run board"}]
        return []

    async def find_slot(self, channel_id: str) -> datetime | None:
        slot = (self._now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        await self._record("integrations.find_slot", "GET", request={"channel_id": channel_id}, response=iso_utc(slot))
        return slot

    # -- media -----------------------------------------------------------------

    async def upload_media(self, media: OutboundMedia) -> UploadedMedia:
        path, size = check_media_file(media)
        filename, mime = upload_filename(path, media.mime)
        digest = hashlib.sha1(f"{path.resolve()}:{size}".encode("utf-8")).hexdigest()[:16]
        uploaded = UploadedMedia(external_id=f"dry_media_{digest}", external_path=f"dry-run://media/{digest}/{filename}")
        await self._record("media.upload", "POST", request={"file": filename, "mime": mime, "bytes": size},
                           response={"id": uploaded.external_id, "path": uploaded.external_path})
        return uploaded

    async def media_status(self, external_id: str) -> UploadedMedia:
        await self._record("media.status", "GET", request={"id": external_id})
        return UploadedMedia(external_id=external_id, external_path=f"dry-run://media/{external_id}", status="ready")

    # -- posts -----------------------------------------------------------------

    async def create_posts(self, post: OutboundPost) -> list[PublishedTarget]:
        now = self._now()
        plan = build_create_payload(
            post, short_links=post.short_links or self.settings.short_links,
            tag_posts=self.settings.tag_posts, now=now,
        )
        state = "DRAFT" if post.mode == "draft" else "QUEUE"
        stored = await self._load()
        published: list[PublishedTarget] = []
        for target, item in zip(post.targets, plan.body["posts"]):
            external_id = f"dry_{uuid.uuid4().hex[:20]}"
            stored.append({
                "id": external_id, "channel_id": target.channel_id, "provider": target.provider,
                "content": target.caption, "publish_date": iso_utc(plan.date), "state": state,
                "group": plan.groups[target.target_id], "created_at": iso_utc(now),
                "settings": item.get("settings") or {}, "value": item.get("value") or [],
                "target_id": target.target_id, "post_id": post.post_id,
            })
            published.append(PublishedTarget(
                target_id=target.target_id, channel_id=target.channel_id,
                external_id=external_id, external_group=plan.groups[target.target_id],
            ))
        await self._save(stored)
        await self._record("posts.create", "POST", request=plan.body,
                           response=[{"postId": p.external_id, "integration": p.channel_id} for p in published])
        return published

    async def find_existing_post(
        self, channel_id: str, scheduled_at: datetime, caption: str, *, window_minutes: int = 10,
    ) -> ExternalPost | None:
        at = ensure_utc(scheduled_at, "scheduled_at")
        window = timedelta(minutes=max(1, int(window_minutes)))
        return pick_existing(await self.list_posts(at - window, at + window), channel_id, at, caption)

    async def list_posts(self, start: datetime, end: datetime) -> list[ExternalPost]:
        start, end = ensure_utc(start, "start"), ensure_utc(end, "end")
        now = self._now()
        posts = [self._to_external(record, now) for record in await self._load()]
        selected = [p for p in posts if p.publish_date is not None and start <= p.publish_date <= end]
        await self._record("posts.list", "GET", request={"startDate": iso_utc(start), "endDate": iso_utc(end)},
                           response={"count": len(selected)})
        return selected

    async def delete_post(self, external_id: str) -> None:
        stored = await self._load()
        await self._save([record for record in stored if record.get("id") != external_id])
        await self._record("posts.delete", "DELETE", request={"id": external_id})

    async def set_post_status(self, external_id: str, status: Literal["draft", "schedule"]) -> None:
        if status not in ("draft", "schedule"):
            raise ValidationError(f"Post status must be 'draft' or 'schedule', got {status!r}.")
        stored = await self._load()
        record = self._find(stored, external_id)
        record["state"] = "DRAFT" if status == "draft" else "QUEUE"
        await self._save(stored)
        await self._record("posts.status", "PUT", request={"id": external_id, "status": status})

    async def update_post_settings(self, external_id: str, settings: dict[str, Any]) -> None:
        stored = await self._load()
        record = self._find(stored, external_id)
        if self._to_external(record, self._now()).state == "PUBLISHED":
            raise ValidationError(f"Dry-run post {external_id} is already published; its settings cannot change.")
        patch = {k: v for k, v in (settings or {}).items() if k not in ("__type", "channel_name")}
        record["settings"] = {**(record.get("settings") or {}), **patch}
        await self._save(stored)
        await self._record("posts.settings", "PUT", request={"id": external_id, "settings": patch})

    @staticmethod
    def _find(stored: list[dict[str, Any]], external_id: str) -> dict[str, Any]:
        for record in stored:
            if record.get("id") == external_id:
                return record
        raise NotFoundError(f"Dry-run post {external_id} does not exist.")

    # -- analytics & notifications ---------------------------------------------

    async def post_metrics(self, external_id: str, days: int = 7) -> PostMetrics:
        await self._record("analytics.post", "GET", request={"id": external_id, "date": days})
        return PostMetrics(metrics={key: 0 for key in CANONICAL_METRICS}, raw={"dry_run": True})

    async def channel_metrics(self, channel_id: str, days: int = 30) -> list[dict[str, Any]]:
        await self._record("analytics.platform", "GET", request={"id": channel_id, "date": days})
        return []

    async def notifications(self, page: int = 0) -> list[PublisherNotification]:
        await self._record("notifications.list", "GET", request={"page": page})
        return []

    async def generate_video(
        self, *, kind: str, prompt: str, orientation: str = "vertical",
        image_urls: list[str] | None = None, voice: str = "",
    ) -> list[UploadedMedia]:
        kind = (kind or "").strip().lower()
        if kind not in VIDEO_KINDS:
            raise ValidationError(f"AI video kind must be one of {', '.join(VIDEO_KINDS)}, got {kind!r}.")
        if orientation not in VIDEO_ORIENTATIONS:
            raise ValidationError(f"AI video orientation must be vertical or horizontal, got {orientation!r}.")
        if not (prompt or "").strip():
            raise ValidationError("AI video needs a prompt.")
        await enforce_video_budget(SERVICE, self.settings.ai_video_monthly_budget)
        video_id = f"dry_video_{uuid.uuid4().hex[:16]}"
        result = UploadedMedia(external_id=video_id, external_path=f"dry-run://video/{video_id}.mp4")
        await self._record(VIDEO_OPERATION, "POST",
                           request={"type": kind, "output": orientation, "prompt": prompt, "images": image_urls or []},
                           response={"id": video_id})
        return [result]


__all__ = ["DRY_RUN_CHANNELS", "DryRunPublisher"]
