"""Typed low-level client for the Postiz public API (docs/social-engine/postiz/openapi.json).

One method per endpoint, no business rules: budgets, validation and mapping to
our own types live in `postiz.py`. Every call goes through `ServiceClient`, so
it is recorded in the `social_api_calls` ledger under service "postiz" and an
operation name such as "posts.create" (used by the Activity page and budgets).

Idempotency follows what a duplicate request would do on Postiz:

* GET / PUT / DELETE are idempotent and retried on 429/5xx/network errors.
* `posts.create`, `video.generate` and `clipping.create` are NOT idempotent
  (they publish or spend credits): an ambiguous failure raises
  `UncertainOutcome` and the caller must reconcile.
* Uploads and lookup-style POSTs (`integrations.trigger`, `video.function`)
  are marked idempotent because repeating them is harmless.

Auth: `Authorization: <api key>` — no "Bearer" prefix.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, TypedDict
from urllib.parse import quote

from ..errors import ConfigurationError, ExternalServiceError, UncertainOutcome
from ..http import CallContext, ServiceClient

SERVICE = "postiz"
DEFAULT_BASE_URL = "https://api.postiz.com/public/v1"
UPLOAD_TIMEOUT_SECONDS = 900.0
VIDEO_GENERATION_TIMEOUT_SECONDS = 900.0


# ---------------------------------------------------------------------------
# Response / request shapes (from the OpenAPI components)
# ---------------------------------------------------------------------------

class Customer(TypedDict, total=False):
    id: str
    name: str


class Integration(TypedDict, total=False):
    id: str
    name: str
    identifier: str
    picture: str
    disabled: bool
    profile: str
    customer: Customer | None


class Group(TypedDict, total=False):
    id: str
    name: str


class ChannelSettingsInfo(TypedDict, total=False):
    rules: str
    maxLength: int
    settings: Any
    tools: list[dict[str, Any]]


class Me(TypedDict, total=False):
    organization: dict[str, Any]
    user: dict[str, Any] | None


class MediaRef(TypedDict, total=False):
    id: str
    path: str
    thumbnailTimestamp: float


class Tag(TypedDict):
    value: str
    label: str


class PostContent(TypedDict, total=False):
    content: str
    id: str
    image: list[MediaRef]


class PostItem(TypedDict, total=False):
    integration: dict[str, str]
    value: list[PostContent]
    group: str
    settings: dict[str, Any]


class CreatePostRequest(TypedDict, total=False):
    type: Literal["draft", "schedule", "now"]
    date: str
    shortLink: bool
    tags: list[Tag]
    posts: list[PostItem]
    order: str
    inter: float


class CreatePostResult(TypedDict, total=False):
    postId: str
    integration: str


class PostIntegration(TypedDict, total=False):
    id: str
    providerIdentifier: str
    name: str
    picture: str


class PostizPost(TypedDict, total=False):
    id: str
    content: str
    settings: dict[str, Any]
    publishDate: str
    releaseURL: str
    state: str
    error: str | None
    integration: PostIntegration
    group: str


class MissingContentItem(TypedDict, total=False):
    id: str
    url: str


class AnalyticsPoint(TypedDict, total=False):
    total: str
    date: str


class AnalyticsSeries(TypedDict, total=False):
    label: str
    data: list[AnalyticsPoint]
    percentageChange: float


class MediaFile(TypedDict, total=False):
    id: str
    name: str
    path: str
    organizationId: str
    createdAt: str
    updatedAt: str
    status: str


class MediaStatus(TypedDict, total=False):
    id: str
    path: str
    status: str
    warning: str
    error: str


class MediaListItem(TypedDict, total=False):
    id: str
    name: str
    originalName: str | None
    path: str
    createdAt: str


class MediaPage(TypedDict, total=False):
    pages: int
    results: list[MediaListItem]


class Notification(TypedDict, total=False):
    id: str
    content: str
    link: str | None
    createdAt: str


class NotificationsPage(TypedDict, total=False):
    notifications: list[Notification]
    total: int
    page: int
    limit: int
    hasMore: bool


class GeneratedVideo(TypedDict, total=False):
    id: str
    path: str


class Voice(TypedDict, total=False):
    id: str
    name: str


class ClippingClip(TypedDict, total=False):
    id: str
    title: str
    content: str
    start: float
    end: float
    status: str
    error: str | None
    mediaId: str | None
    path: str | None
    thumbnail: str | None


class Clipping(TypedDict, total=False):
    id: str
    url: str
    status: str
    error: str | None
    title: str | None
    thumbnail: str | None
    duration: int | None
    createdAt: str
    clips: list[ClippingClip]


class ClippingPage(TypedDict, total=False):
    pages: int
    results: list[Clipping]


VideoType = Literal["image-text-slides", "veo3"]
VideoOutput = Literal["vertical", "horizontal"]


def _seg(value: str) -> str:
    """Escape one path segment (ids are opaque strings)."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Postiz path parameter must not be empty")
    return quote(text, safe="")


def iso_utc(value: datetime) -> str:
    """Postiz date format: `2024-12-14T10:00:00.000Z`."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Postiz dates must be timezone-aware, got {value.isoformat()}")
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _as_list(body: Any, key: str = "") -> list:
    if isinstance(body, list):
        return body
    if key and isinstance(body, dict) and isinstance(body.get(key), list):
        return body[key]
    return []


class PostizClient:
    """One instance per job/request; close it with `aclose()` (or `async with`)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        ctx: CallContext | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_base: float = 1.5,
    ) -> None:
        key = (api_key or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()   # Postiz rejects "Bearer <key>"; accept it pasted that way
        if not key:
            raise ConfigurationError("Postiz API key is empty.")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.ctx = ctx or CallContext()
        self._http = ServiceClient(
            SERVICE,
            self.base_url,
            headers={"Authorization": key, "Accept": "application/json"},
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_base=backoff_base,
        )

    async def __aenter__(self) -> "PostizClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, *, operation: str, **kwargs: Any) -> Any:
        return await self._http.json(method, path, operation=operation, ctx=self.ctx, **kwargs)

    # -- account --------------------------------------------------------------

    async def is_connected(self) -> bool:
        body = await self._call("GET", "/is-connected", operation="auth.is_connected")
        return bool(isinstance(body, dict) and body.get("connected"))

    async def me(self) -> Me:
        body = await self._call("GET", "/me", operation="auth.me")
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]

    # -- integrations (channels) ---------------------------------------------

    async def list_integrations(self, *, group: str = "") -> list[Integration]:
        params = {"group": group} if group else None
        body = await self._call("GET", "/integrations", operation="integrations.list", params=params)
        return [item for item in _as_list(body, "integrations") if isinstance(item, dict)]

    async def list_groups(self) -> list[Group]:
        body = await self._call("GET", "/groups", operation="groups.list")
        return [item for item in _as_list(body, "groups") if isinstance(item, dict)]

    async def delete_integration(self, integration_id: str) -> dict[str, Any]:
        """Disconnect a channel. Postiz also deletes its scheduled posts."""
        body = await self._call("DELETE", f"/integrations/{_seg(integration_id)}", operation="integrations.delete")
        return body if isinstance(body, dict) else {}

    async def integration_oauth_url(self, provider: str, *, refresh: str = "") -> str:
        params = {"refresh": refresh} if refresh else None
        body = await self._call("GET", f"/social/{_seg(provider)}", operation="integrations.oauth_url", params=params)
        return str((body or {}).get("url") or "") if isinstance(body, dict) else ""

    async def integration_settings(self, integration_id: str) -> ChannelSettingsInfo:
        body = await self._call(
            "GET", f"/integration-settings/{_seg(integration_id)}", operation="integrations.settings",
        )
        if isinstance(body, dict) and isinstance(body.get("output"), dict):
            return body["output"]  # type: ignore[return-value]
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]

    async def trigger_integration_tool(
        self, integration_id: str, method_name: str, data: Mapping[str, str] | None = None,
    ) -> Any:
        """Run a provider tool (audio search, list boards/flairs...). Tools are lookups → idempotent."""
        body = await self._call(
            "POST", f"/integration-trigger/{_seg(integration_id)}", operation="integrations.trigger",
            idempotent=True, json={"methodName": method_name, "data": dict(data or {})},
        )
        if isinstance(body, dict) and "output" in body:
            return body["output"]
        return body

    async def find_slot(self, integration_id: str) -> str | None:
        body = await self._call("GET", f"/find-slot/{_seg(integration_id)}", operation="integrations.find_slot")
        if isinstance(body, dict) and body.get("date"):
            return str(body["date"])
        return None

    # -- posts ----------------------------------------------------------------

    async def list_posts(self, start: datetime, end: datetime, *, customer: str = "") -> list[PostizPost]:
        params: dict[str, str] = {"startDate": iso_utc(start), "endDate": iso_utc(end)}
        if customer:
            params["customer"] = customer
        body = await self._call("GET", "/posts", operation="posts.list", params=params)
        return [item for item in _as_list(body, "posts") if isinstance(item, dict)]

    async def create_posts(self, body: CreatePostRequest | Mapping[str, Any]) -> list[CreatePostResult]:
        """POST /posts — never retried after the request may have reached Postiz.

        A 2xx answer we cannot read is an `UncertainOutcome`: the posts were
        probably created but we do not know their ids.
        """
        response = await self._http.request(
            "POST", "/posts", operation="posts.create", ctx=self.ctx, idempotent=False, json=dict(body),
        )
        try:
            parsed = response.json()
        except ValueError as exc:
            raise UncertainOutcome(
                f"postiz posts.create returned HTTP {response.status_code} with a body that is not JSON; "
                "the posts may exist. Reconcile before retrying.",
                details={"status_code": response.status_code, "body": response.text[:400]},
            ) from exc
        if isinstance(parsed, dict) and isinstance(parsed.get("posts"), list):
            parsed = parsed["posts"]
        elif isinstance(parsed, dict) and parsed.get("postId"):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise UncertainOutcome(
                "postiz posts.create returned an unexpected body; the posts may exist. Reconcile before retrying.",
                details={"status_code": response.status_code, "body": response.text[:400]},
            )
        return [item for item in parsed if isinstance(item, dict)]

    async def delete_post(self, post_id: str, *, missing_ok: bool = True) -> dict[str, Any]:
        """Delete a post. NOTE: Postiz deletes every post that shares its `group`."""
        response = await self._http.request(
            "DELETE", f"/posts/{_seg(post_id)}", operation="posts.delete", ctx=self.ctx,
            ok_statuses=(404,) if missing_ok else (),
        )
        if response.status_code == 404 or not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    async def delete_post_group(self, group: str) -> dict[str, Any]:
        body = await self._call("DELETE", f"/posts/group/{_seg(group)}", operation="posts.delete_group")
        return body if isinstance(body, dict) else {}

    async def missing_content(self, post_id: str) -> list[MissingContentItem]:
        body = await self._call("GET", f"/posts/{_seg(post_id)}/missing", operation="posts.missing")
        return [item for item in _as_list(body) if isinstance(item, dict)]

    async def update_release_id(self, post_id: str, release_id: str) -> dict[str, Any]:
        body = await self._call(
            "PUT", f"/posts/{_seg(post_id)}/release-id", operation="posts.release_id",
            json={"releaseId": release_id},
        )
        return body if isinstance(body, dict) else {}

    async def change_post_status(self, post_id: str, status: Literal["draft", "schedule"]) -> dict[str, Any]:
        body = await self._call(
            "PUT", f"/posts/{_seg(post_id)}/status", operation="posts.status", json={"status": status},
        )
        return body if isinstance(body, dict) else {}

    async def update_post_settings(self, post_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        body = await self._call(
            "PUT", f"/posts/{_seg(post_id)}/settings", operation="posts.settings",
            json={"settings": dict(settings)},
        )
        return body if isinstance(body, dict) else {}

    # -- analytics ------------------------------------------------------------

    async def platform_analytics(self, integration_id: str, days: int) -> list[AnalyticsSeries]:
        body = await self._call(
            "GET", f"/analytics/{_seg(integration_id)}", operation="analytics.platform",
            params={"date": str(int(days))},
        )
        return [item for item in _as_list(body) if isinstance(item, dict)]

    async def post_analytics(self, post_id: str, days: int) -> list[AnalyticsSeries]:
        body = await self._call(
            "GET", f"/analytics/post/{_seg(post_id)}", operation="analytics.post",
            params={"date": str(int(days))},
        )
        return [item for item in _as_list(body) if isinstance(item, dict)]

    # -- media ----------------------------------------------------------------

    async def upload_file(
        self,
        source: str | Path | bytes,
        *,
        filename: str,
        mime: str,
        adjust: bool = False,
        timeout: float = UPLOAD_TIMEOUT_SECONDS,
    ) -> MediaFile:
        """Multipart upload. A duplicate upload only adds a library item → idempotent."""
        form = {"adjust": "true" if adjust else "false"}
        if isinstance(source, bytes):
            body = await self._call(
                "POST", "/upload", operation="media.upload", idempotent=True, data=form,
                files={"file": (filename, source, mime)}, timeout=timeout,
            )
        else:
            with open(source, "rb") as handle:   # httpx rewinds the handle on every attempt
                body = await self._call(
                    "POST", "/upload", operation="media.upload", idempotent=True, data=form,
                    files={"file": (filename, handle, mime)}, timeout=timeout,
                )
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]

    async def upload_from_url(self, url: str, *, adjust: bool = False) -> MediaFile:
        body = await self._call(
            "POST", "/upload-from-url", operation="media.upload_url", idempotent=True,
            json={"url": url, "adjust": bool(adjust)}, timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]

    async def media_status(self, media_id: str) -> MediaStatus:
        body = await self._call("GET", f"/media/{_seg(media_id)}/status", operation="media.status")
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]

    async def list_media(self, *, page: int = 1, search: str = "") -> MediaPage:
        params: dict[str, Any] = {"page": max(1, int(page))}
        if search:
            params["search"] = search
        body = await self._call("GET", "/media", operation="media.list", params=params)
        return body if isinstance(body, dict) else {"pages": 0, "results": []}  # type: ignore[return-value]

    # -- notifications --------------------------------------------------------

    async def list_notifications(self, *, page: int = 0) -> NotificationsPage:
        body = await self._call(
            "GET", "/notifications", operation="notifications.list", params={"page": max(0, int(page))},
        )
        if isinstance(body, list):
            return {"notifications": body, "page": page}
        return body if isinstance(body, dict) else {"notifications": []}  # type: ignore[return-value]

    # -- AI video -------------------------------------------------------------

    async def generate_video(
        self, video_type: VideoType, output: VideoOutput, custom_params: Mapping[str, Any],
    ) -> list[GeneratedVideo]:
        """Spends one AI video credit → not idempotent, long timeout."""
        body = await self._call(
            "POST", "/generate-video", operation="video.generate", idempotent=False,
            json={"type": video_type, "output": output, "customParams": dict(custom_params)},
            timeout=VIDEO_GENERATION_TIMEOUT_SECONDS,
        )
        if isinstance(body, dict):
            body = [body] if body.get("path") else body.get("videos") or body.get("results") or []
        return [item for item in _as_list(body) if isinstance(item, dict)]

    async def video_function(
        self, function_name: str, identifier: str, params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"functionName": function_name, "identifier": identifier}
        if params:
            payload["params"] = dict(params)
        body = await self._call("POST", "/video/function", operation="video.function", idempotent=True, json=payload)
        return body if isinstance(body, dict) else {"result": body}

    # -- clipping -------------------------------------------------------------

    async def start_clipping(
        self,
        url: str,
        *,
        integrations: Sequence[str] = (),
        clips: int | None = None,
        fit: Literal["blur", "crop"] | None = None,
    ) -> str:
        """Start clipping a YouTube video (spends clipping minutes → not idempotent). Returns the id."""
        payload: dict[str, Any] = {"url": url}
        if integrations:
            payload["integrations"] = list(integrations)
        if clips is not None:
            payload["clips"] = int(clips)
        if fit:
            payload["fit"] = fit
        body = await self._call("POST", "/clipping", operation="clipping.create", idempotent=False, json=payload)
        clipping_id = str((body or {}).get("id") or "") if isinstance(body, dict) else ""
        if not clipping_id:
            raise ExternalServiceError(SERVICE, "clipping.create returned no clipping id", retryable=False)
        return clipping_id

    async def list_clippings(self, *, page: int = 1) -> ClippingPage:
        body = await self._call("GET", "/clipping", operation="clipping.list", params={"page": max(1, int(page))})
        return body if isinstance(body, dict) else {"pages": 0, "results": []}  # type: ignore[return-value]

    async def get_clipping(self, clipping_id: str) -> Clipping:
        body = await self._call("GET", f"/clipping/{_seg(clipping_id)}", operation="clipping.get")
        return body if isinstance(body, dict) else {}  # type: ignore[return-value]


__all__ = [
    "AnalyticsSeries", "ChannelSettingsInfo", "Clipping", "ClippingPage", "CreatePostRequest",
    "CreatePostResult", "DEFAULT_BASE_URL", "GeneratedVideo", "Group", "Integration", "MediaFile",
    "MediaPage", "MediaRef", "MediaStatus", "Me", "MissingContentItem", "Notification",
    "NotificationsPage", "PostContent", "PostItem", "PostizClient", "PostizPost", "Tag", "Voice",
    "iso_utc",
]
