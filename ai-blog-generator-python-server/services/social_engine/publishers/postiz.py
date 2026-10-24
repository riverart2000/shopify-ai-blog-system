"""Production publisher: Postiz Cloud via its public API.

Create-post contract (see `create_posts`):

* One call = one Postiz request = one rate-budget unit, however many targets
  it carries. All targets must share `scheduled_at` (Postiz takes ONE date per
  request); split a post's targets by time before calling.
* The local hourly budget (`settings.postiz.create_post_hourly_budget`) is
  checked against the API ledger before anything is sent → `RateLimited`.
* An ambiguous failure raises `UncertainOutcome` and is never retried here:
  reconcile each target with `find_existing_post` before trying again.
* A 2xx answer that misses some targets raises `PartialPublishError` carrying
  the ids that did come back (`.published`, also in `details["published"]`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Literal, Mapping, Sequence

from ..context import StoreContext
from ..errors import ConfigurationError, ExternalServiceError, RateLimited, UncertainOutcome, ValidationError
from ..http import CallContext
from .base import (
    ChannelRules, ExternalPost, OutboundMedia, OutboundPost, OutboundTarget, PostMetrics, PublishedTarget,
    PublisherChannel, PublisherNotification, UploadedMedia,
)
from .common import (
    CREATE_OPERATION, CREATE_WINDOW_SECONDS, build_create_payload, check_media_file, enforce_video_budget,
    ensure_utc, media_is_video, parse_datetime, pick_existing, upload_filename, utc_now, window_usage,
)
from .postiz_client import (
    AnalyticsSeries, CreatePostResult, Integration, MediaFile, MediaStatus, PostizClient, PostizPost, iso_utc,
)

logger = logging.getLogger("ai_blog_server.social_engine.publishers")

SERVICE = "postiz"
# Wait up to ~4 minutes for Postiz to adjust an uploaded video.
MEDIA_POLL_DELAYS: tuple[float, ...] = (2, 3, 5, 5, 10, 10, 15, 15, 20, 30, 30, 30, 30, 30)
VIDEO_KINDS = ("image-text-slides", "veo3")
VIDEO_ORIENTATIONS = ("vertical", "horizontal")

CANONICAL_METRICS = ("impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks", "engagement")
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "impressions": ("impressions", "impression"),
    "reach": ("reach",),
    "views": ("views", "view", "plays", "play", "video views", "video view"),
    "likes": ("likes", "like", "reactions", "reaction"),
    "comments": ("comments", "comment", "replies", "reply"),
    "shares": ("shares", "share", "reposts", "repost", "retweets", "retweet", "quotes", "quote"),
    "saves": ("saves", "save", "bookmarks", "bookmark"),
    "clicks": ("clicks", "click", "pin clicks", "pin click", "outbound clicks", "outbound click"),
    "engagement": ("engagement", "engagements"),
}
LABEL_TO_METRIC: dict[str, str] = {alias: key for key, aliases in _METRIC_ALIASES.items() for alias in aliases}

_ERROR_WORDS = ("error", "fail", "disconnect", "expired", "banned", "rejected", "unauthori", "reconnect", "revoked")
_WARNING_WORDS = ("warning", "limit", "quota", "credit", "almost", "retry", "delayed")
_HTML_TAG = re.compile(r"<[^>]+>")


class PartialPublishError(ExternalServiceError):
    """Postiz answered 2xx but did not return a post id for every target.

    `published` holds the targets that DID get an id (persist them!). The
    missing ones must be treated as uncertain and reconciled with
    `find_existing_post`, never re-sent blindly.
    """

    def __init__(self, message: str, *, published: list[PublishedTarget], missing_target_ids: list[str],
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(
            SERVICE, message, retryable=False,
            details={
                **(details or {}),
                "published": [asdict(item) for item in published],
                "missing_target_ids": list(missing_target_ids),
                "reconcile": True,
            },
        )
        self.published = published
        self.missing_target_ids = list(missing_target_ids)


# ---------------------------------------------------------------------------
# Pure mapping helpers (unit-testable without HTTP)
# ---------------------------------------------------------------------------

def canonical_metric(label: str) -> str | None:
    key = " ".join(re.sub(r"[_\-]+", " ", str(label or "")).split()).casefold()
    return LABEL_TO_METRIC.get(key)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _points(series: Mapping[str, Any]) -> list[tuple[str, float]]:
    points = [
        (str(point.get("date") or ""), _number(point.get("total")))
        for point in (series.get("data") or []) if isinstance(point, Mapping)
    ]
    valid = [(day, value) for day, value in points if value is not None]
    return sorted(valid, key=lambda item: item[0]) if all(day for day, _ in valid) else valid


def series_total(series: Mapping[str, Any]) -> float | None:
    """Last value of a cumulative (non-decreasing) series, otherwise the sum of daily values."""
    values = [value for _, value in _points(series)]
    if not values:
        return None
    if all(later >= earlier for earlier, later in zip(values, values[1:])):
        return values[-1]
    return sum(values)


def map_post_metrics(raw: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Postiz analytics labels → canonical metric counts. Rates/averages stay in `raw` only."""
    metrics: dict[str, int] = {}
    for series in raw:
        if not isinstance(series, Mapping):
            continue
        key = canonical_metric(str(series.get("label") or ""))
        if not key:
            continue
        values = [value for _, value in _points(series)]
        if not values or any(not value.is_integer() for value in values):
            continue
        total = series_total(series)
        if total is not None:
            metrics[key] = metrics.get(key, 0) + int(total)
    return metrics


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").casefold()).strip("_") or "metric"


def map_channel_metrics(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series in raw:
        if not isinstance(series, Mapping):
            continue
        label = str(series.get("label") or "")
        metric = canonical_metric(label) or _slug(label)
        for day, value in _points(series):
            rows.append({
                "metric": metric, "label": label, "day": day,
                "value": int(value) if value.is_integer() else value,
            })
    return rows


def classify_notification(content: str) -> str:
    text = _HTML_TAG.sub(" ", str(content or "")).casefold()
    if any(word in text for word in _ERROR_WORDS):
        return "error"
    if any(word in text for word in _WARNING_WORDS):
        return "warning"
    return "info"


def external_post_from_postiz(item: PostizPost | Mapping[str, Any]) -> ExternalPost:
    integration = item.get("integration") or {}
    if not isinstance(integration, Mapping):
        integration = {"id": str(integration)}
    return ExternalPost(
        external_id=str(item.get("id") or ""),
        channel_id=str(integration.get("id") or item.get("integrationId") or ""),
        provider=str(integration.get("providerIdentifier") or integration.get("identifier") or ""),
        content=str(item.get("content") or ""),
        publish_date=parse_datetime(item.get("publishDate")),
        state=str(item.get("state") or "").upper(),
        release_url=str(item.get("releaseURL") or ""),
        error=str(item.get("error") or ""),
        group=str(item.get("group") or ""),
    )


def channel_from_integration(item: Integration | Mapping[str, Any]) -> PublisherChannel:
    customer = item.get("customer") or {}
    if not isinstance(customer, Mapping):
        customer = {}
    return PublisherChannel(
        channel_id=str(item.get("id") or ""),
        provider=str(item.get("identifier") or item.get("providerIdentifier") or ""),
        name=str(item.get("name") or ""),
        profile=str(item.get("profile") or ""),
        picture=str(item.get("picture") or ""),
        disabled=bool(item.get("disabled")),
        customer_id=str(customer.get("id") or ""),
        customer_name=str(customer.get("name") or ""),
    )


def _tool_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, default=str, separators=(",", ":"))


def _media_ref_id(url: str) -> str:
    return "url-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

class PostizPublisher:
    name = "postiz"

    def __init__(
        self,
        sctx: StoreContext,
        ctx: CallContext | None = None,
        *,
        client: PostizClient | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        media_poll_delays: Sequence[float] = MEDIA_POLL_DELAYS,
    ) -> None:
        self.sctx = sctx
        self.settings = sctx.settings.postiz
        self.ctx = ctx or sctx.call_context()
        self._sleep = sleep
        self._media_poll_delays = tuple(media_poll_delays)
        if client is None:
            api_key = self.settings.api_key()
            if not api_key:
                env = self.settings.api_key_env or "POSTIZ_API_KEY"
                raise ConfigurationError(
                    f"Postiz is not connected: the {env} environment variable is not set. "
                    "Create an API key in Postiz → Settings → Public API, add it to the server "
                    "environment and restart, or switch this store to the dry-run publisher.",
                    details={"env": env, "store_id": sctx.store_id},
                )
            client = PostizClient(api_key, base_url=self.settings.base_url, ctx=self.ctx)
        self.client = client

    async def __aenter__(self) -> "PostizPublisher":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    def _log(self, level: int, message: str, *args: Any) -> None:
        logger.log(level, message, *args, extra={
            "operation": "social_engine.publishers.postiz",
            "store_id": self.sctx.store_id,
            "correlation_id": self.ctx.correlation_id,
        })

    # -- account & channels ---------------------------------------------------

    async def check_connection(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "publisher": self.name, "base_url": self.client.base_url, "customer_id": self.settings.customer_id,
        }
        try:
            connected = await self.client.is_connected()
        except ExternalServiceError as exc:
            if exc.status_code in (401, 403):
                return {**result, "connected": False, "error": "Postiz rejected the API key (HTTP "
                        f"{exc.status_code}). Check {self.settings.api_key_env}."}
            raise
        result["connected"] = connected
        if connected:
            me = await self.client.me()
            result["organization"] = me.get("organization") or {}
            result["user"] = me.get("user")
        used, _ = await window_usage(SERVICE, CREATE_OPERATION, CREATE_WINDOW_SECONDS)
        result["create_post_budget"] = {"used_last_hour": used, "hourly_budget": self.settings.create_post_hourly_budget}
        return result

    async def list_channels(self) -> list[PublisherChannel]:
        customer_id = self.settings.customer_id
        integrations = await self.client.list_integrations(group=customer_id)
        channels: list[PublisherChannel] = []
        for item in integrations:
            if customer_id and "customer" in item and ((item.get("customer") or {}).get("id") or "") != customer_id:
                continue   # the API ignored the group filter: filter locally
            channel = channel_from_integration(item)
            if channel.channel_id:
                channels.append(channel)
        return channels

    async def list_groups(self) -> list[dict[str, Any]]:
        return [dict(group) for group in await self.client.list_groups()]

    async def channel_rules(self, channel_id: str) -> ChannelRules:
        info = await self.client.integration_settings(channel_id)
        max_length = _number(info.get("maxLength"))
        return ChannelRules(
            max_length=int(max_length) if max_length else None,
            rules=str(info.get("rules") or ""),
            settings_schema=info.get("settings"),
            tools=[tool for tool in (info.get("tools") or []) if isinstance(tool, dict)],
        )

    async def run_channel_tool(self, channel_id: str, method: str, data: dict[str, Any]) -> Any:
        if not method:
            raise ValidationError("A channel tool method name is required.")
        # The trigger endpoint takes string values only.
        params = {str(key): _tool_value(value) for key, value in (data or {}).items() if value is not None}
        return await self.client.trigger_integration_tool(channel_id, method, params)

    async def find_slot(self, channel_id: str) -> datetime | None:
        return parse_datetime(await self.client.find_slot(channel_id))

    # -- media -----------------------------------------------------------------

    async def upload_media(self, media: OutboundMedia) -> UploadedMedia:
        path, size = check_media_file(media)
        video = media_is_video(media)
        filename, mime = upload_filename(path, media.mime)
        adjust = video and self.settings.upload_adjust_video
        timeout = 900.0 if video else 120.0
        uploaded = await self.client.upload_file(path, filename=filename, mime=mime, adjust=adjust, timeout=timeout)
        self._log(logging.INFO, "Uploaded %s (%d bytes, adjust=%s) to Postiz as %s",
                  media.asset_id, size, adjust, uploaded.get("id"))
        return await self._settle_upload(uploaded, media.asset_id)

    async def upload_from_url(self, url: str, *, adjust: bool = False, asset_id: str = "") -> UploadedMedia:
        """Postiz extra: let Postiz fetch a public URL instead of uploading bytes."""
        uploaded = await self.client.upload_from_url(url, adjust=adjust)
        return await self._settle_upload(uploaded, asset_id or url)

    async def media_status(self, external_id: str) -> UploadedMedia:
        """Postiz extra: current processing state of an uploaded file."""
        return self._uploaded_from_status(await self.client.media_status(external_id), external_id, "", external_id)

    async def _settle_upload(self, uploaded: MediaFile, asset_id: str) -> UploadedMedia:
        media_id = str(uploaded.get("id") or "")
        media_path = str(uploaded.get("path") or "")
        if not media_id or not media_path:
            raise ExternalServiceError(
                SERVICE, f"media.upload returned no id/path for asset {asset_id}: {str(dict(uploaded))[:300]}",
                retryable=True,   # a duplicate upload is harmless
            )
        if str(uploaded.get("status") or "ready").lower() != "processing":
            return UploadedMedia(external_id=media_id, external_path=media_path, status="ready")
        for delay in self._media_poll_delays:
            await self._sleep(delay)
            current = self._uploaded_from_status(await self.client.media_status(media_id), media_id, media_path, asset_id)
            if current.status != "processing":
                return current
        self._log(logging.WARNING, "Postiz is still processing media %s for asset %s; publish once it is ready",
                  media_id, asset_id)
        return UploadedMedia(external_id=media_id, external_path=media_path, status="processing")

    def _uploaded_from_status(self, status: MediaStatus, media_id: str, fallback_path: str, asset_id: str) -> UploadedMedia:
        state = str(status.get("status") or "processing").lower()
        path = str(status.get("path") or fallback_path)
        if state == "failed":
            raise ExternalServiceError(
                SERVICE,
                f"Postiz could not process the media for asset {asset_id}: "
                f"{status.get('error') or status.get('warning') or 'unknown error'}",
                retryable=False, details={"media_id": media_id, "asset_id": asset_id},
            )
        warning = str(status.get("warning") or "")
        if state == "ready" and warning:
            self._log(logging.WARNING, "Postiz kept the original file for asset %s: %s", asset_id, warning)
        return UploadedMedia(
            external_id=str(status.get("id") or media_id), external_path=path,
            status="ready" if state == "ready" else "processing", warning=warning,
        )

    async def list_media(self, *, page: int = 1, search: str = "") -> dict[str, Any]:
        return dict(await self.client.list_media(page=page, search=search))

    # -- posts -----------------------------------------------------------------

    async def create_posts(self, post: OutboundPost) -> list[PublishedTarget]:
        plan = build_create_payload(
            post,
            short_links=post.short_links or self.settings.short_links,
            tag_posts=self.settings.tag_posts,
            now=utc_now(),
        )
        await self._enforce_create_budget()
        try:
            results = await self.client.create_posts(plan.body)
        except UncertainOutcome as exc:
            # Everything the reconciler needs: the date actually sent (≠ scheduled_at for "now").
            exc.details.update({
                "post_id": post.post_id,
                "sent_date": plan.body["date"],
                "targets": [
                    {"target_id": t.target_id, "channel_id": t.channel_id, "group": plan.groups[t.target_id]}
                    for t in post.targets
                ],
            })
            self._log(logging.WARNING, "Postiz create for post %s is uncertain (%s); reconcile before retrying",
                      post.post_id, exc.message)
            raise
        published, missing = self._map_results(post, plan.groups, results)
        if missing:
            names = ", ".join(f"{t.provider} {t.channel_id} (target {t.target_id})" for t in missing)
            self._log(logging.ERROR, "Postiz create for post %s returned no id for: %s; returned ids: %s",
                      post.post_id, names, [p.external_id for p in published])
            raise PartialPublishError(
                f"posts.create returned no post id for {len(missing)} of {len(post.targets)} channel(s): {names}. "
                "Those targets may still have been created — reconcile them before retrying.",
                published=published,
                missing_target_ids=[t.target_id for t in missing],
                details={"post_id": post.post_id, "response": [dict(r) for r in results][:50]},
            )
        self._log(logging.INFO, "Created %d Postiz post(s) for post %s (%s at %s)",
                  len(published), post.post_id, post.mode, plan.body["date"])
        return published

    def _map_results(
        self, post: OutboundPost, groups: dict[str, str], results: list[CreatePostResult],
    ) -> tuple[list[PublishedTarget], list[OutboundTarget]]:
        by_channel: dict[str, list[str]] = {}
        unlabelled: list[str] = []
        for item in results:
            post_id = str(item.get("postId") or item.get("id") or "")
            if not post_id:
                continue
            integration = item.get("integration")
            if isinstance(integration, Mapping):
                integration = integration.get("id")
            if integration:
                by_channel.setdefault(str(integration), []).append(post_id)
            else:
                unlabelled.append(post_id)
        # Responses without integration ids are mapped by position (Postiz answers in request order).
        positional = len(unlabelled) == len(results) == len(post.targets)
        published: list[PublishedTarget] = []
        missing: list[OutboundTarget] = []
        for index, target in enumerate(post.targets):
            ids = by_channel.get(target.channel_id)
            external_id = ids.pop(0) if ids else (unlabelled[index] if positional else "")
            if external_id:
                published.append(PublishedTarget(
                    target_id=target.target_id, channel_id=target.channel_id,
                    external_id=external_id, external_group=groups.get(target.target_id, ""),
                ))
            else:
                missing.append(target)
        return published, missing

    async def _enforce_create_budget(self) -> None:
        budget = self.settings.create_post_hourly_budget
        used, oldest = await window_usage(SERVICE, CREATE_OPERATION, CREATE_WINDOW_SECONDS)
        if used < budget:
            return
        retry_at = (oldest or int(time.time())) + CREATE_WINDOW_SECONDS + 5
        raise RateLimited(
            f"Postiz create-post budget reached: {used}/{budget} requests in the last hour. "
            f"Next request allowed after {iso_utc(datetime.fromtimestamp(retry_at, UTC))}.",
            retry_at=retry_at,
            details={"used": used, "budget": budget},
        )

    async def find_existing_post(
        self, channel_id: str, scheduled_at: datetime, caption: str, *, window_minutes: int = 10,
    ) -> ExternalPost | None:
        at = ensure_utc(scheduled_at, "scheduled_at")
        window = timedelta(minutes=max(1, int(window_minutes)))
        posts = await self.list_posts(at - window, at + window)
        return pick_existing(posts, channel_id, at, caption)

    async def list_posts(self, start: datetime, end: datetime) -> list[ExternalPost]:
        rows = await self.client.list_posts(
            ensure_utc(start, "start"), ensure_utc(end, "end"), customer=self.settings.customer_id,
        )
        return [post for post in map(external_post_from_postiz, rows) if post.external_id]

    async def delete_post(self, external_id: str) -> None:
        """Delete one post (Postiz deletes its whole group, which is this target only)."""
        await self.client.delete_post(external_id, missing_ok=True)

    async def delete_post_group(self, group: str) -> None:
        """Postiz extra: delete every post in a Postiz group."""
        await self.client.delete_post_group(group)

    async def set_post_status(self, external_id: str, status: Literal["draft", "schedule"]) -> None:
        if status not in ("draft", "schedule"):
            raise ValidationError(f"Post status must be 'draft' or 'schedule', got {status!r}.")
        await self.client.change_post_status(external_id, status)

    async def update_post_settings(self, external_id: str, settings: dict[str, Any]) -> None:
        patch = {key: value for key, value in (settings or {}).items() if key not in ("__type", "channel_name")}
        if not patch:
            return
        await self.client.update_post_settings(external_id, patch)

    async def missing_content(self, external_id: str) -> list[dict[str, Any]]:
        """Postiz extra: candidate platform items for a post whose releaseId is "missing"."""
        return [dict(item) for item in await self.client.missing_content(external_id)]

    async def update_release_id(self, external_id: str, release_id: str) -> dict[str, Any]:
        """Postiz extra: connect a post to its real platform item (enables analytics)."""
        if not release_id:
            raise ValidationError("A release id is required.")
        return await self.client.update_release_id(external_id, release_id)

    # -- analytics & notifications ---------------------------------------------

    async def post_metrics(self, external_id: str, days: int = 7) -> PostMetrics:
        raw: list[AnalyticsSeries] = await self.client.post_analytics(external_id, max(1, int(days)))
        return PostMetrics(metrics=map_post_metrics(raw), raw=raw)

    async def channel_metrics(self, channel_id: str, days: int = 30) -> list[dict[str, Any]]:
        return map_channel_metrics(await self.client.platform_analytics(channel_id, max(1, int(days))))

    async def notifications(self, page: int = 0) -> list[PublisherNotification]:
        body = await self.client.list_notifications(page=page)
        items: list[PublisherNotification] = []
        for item in body.get("notifications") or []:
            if not isinstance(item, Mapping):
                continue
            content = str(item.get("content") or "")
            items.append(PublisherNotification(
                external_id=str(item.get("id") or ""),
                created_at=parse_datetime(item.get("createdAt")),
                content=content,
                level=classify_notification(content),
                link=str(item.get("link") or ""),
            ))
        return items

    # -- AI video & clipping ---------------------------------------------------

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
        urls = [url for url in (image_urls or []) if url]
        if kind == "veo3" and len(urls) > 3:
            raise ValidationError(f"veo3 accepts at most 3 reference images, got {len(urls)}.")
        await enforce_video_budget(SERVICE, self.settings.ai_video_monthly_budget)
        custom: dict[str, Any] = {"prompt": prompt.strip()}
        if kind == "image-text-slides" and voice:
            custom["voice"] = voice
        if kind == "veo3":
            custom["images"] = [{"id": _media_ref_id(url), "path": url} for url in urls]
        videos = await self.client.generate_video(kind, orientation, custom)  # type: ignore[arg-type]
        results = [
            UploadedMedia(external_id=str(v.get("id") or ""), external_path=str(v.get("path") or ""), status="ready")
            for v in videos if v.get("path")
        ]
        if not results:
            raise ExternalServiceError(SERVICE, "video.generate returned no video", retryable=False)
        self._log(logging.INFO, "Generated %d AI video(s) with Postiz (%s, %s)", len(results), kind, orientation)
        return results

    async def video_voices(self, identifier: str = "image-text-slides") -> list[dict[str, Any]]:
        """Postiz extra: voices available for image-text-slides videos."""
        body = await self.client.video_function("loadVoices", identifier)
        voices = body.get("voices") if isinstance(body, dict) else None
        return [dict(v) for v in voices or [] if isinstance(v, Mapping)]

    async def start_clipping(
        self, url: str, *, channel_ids: Sequence[str] = (), clips: int | None = None, fit: str | None = None,
    ) -> str:
        """Postiz extra: clip a YouTube video (drafts are created on `channel_ids`). Returns the clipping id."""
        if fit not in (None, "blur", "crop"):
            raise ValidationError(f"Clipping fit must be 'blur' or 'crop', got {fit!r}.")
        if clips is not None and not 1 <= int(clips) <= 10:
            raise ValidationError("Clipping can produce 1–10 clips.")
        return await self.client.start_clipping(url, integrations=list(channel_ids), clips=clips, fit=fit)  # type: ignore[arg-type]

    async def get_clipping(self, clipping_id: str) -> dict[str, Any]:
        return dict(await self.client.get_clipping(clipping_id))

    async def list_clippings(self, page: int = 1) -> dict[str, Any]:
        return dict(await self.client.list_clippings(page=page))


__all__ = [
    "CANONICAL_METRICS", "PartialPublishError", "PostizPublisher", "canonical_metric",
    "channel_from_integration", "classify_notification", "external_post_from_postiz",
    "map_channel_metrics", "map_post_metrics", "series_total",
]
