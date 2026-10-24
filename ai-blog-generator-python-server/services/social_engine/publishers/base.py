"""Publisher contract. Postiz is the production implementation; a dry-run
implementation records payloads without sending anything.

All datetimes are timezone-aware UTC. Implementations must:
* raise `RateLimited` when a budget is exhausted (the job is rescheduled),
* raise `UncertainOutcome` when a create call may have been applied,
* never retry `create_posts` on their own after an ambiguous failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

PublishMode = Literal["schedule", "now", "draft"]


@dataclass
class PublisherChannel:
    channel_id: str
    provider: str
    name: str = ""
    profile: str = ""
    picture: str = ""
    disabled: bool = False
    customer_id: str = ""
    customer_name: str = ""


@dataclass
class ChannelRules:
    max_length: int | None = None
    rules: str = ""
    settings_schema: Any = None
    tools: list[dict] = field(default_factory=list)


@dataclass
class OutboundMedia:
    asset_id: str
    path: str                      # local file path
    mime: str
    kind: str = "image"            # image | video
    external_id: str = ""          # set when already uploaded
    external_path: str = ""


@dataclass
class UploadedMedia:
    external_id: str
    external_path: str
    status: str = "ready"          # ready | processing | failed
    warning: str = ""


@dataclass
class OutboundTarget:
    target_id: str
    channel_id: str
    provider: str
    caption: str
    scheduled_at: datetime
    media: list[OutboundMedia] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)   # extra parts: first comment / thread
    settings: dict[str, Any] = field(default_factory=dict)  # provider settings (without __type)
    title: str = ""
    link_url: str = ""


@dataclass
class OutboundPost:
    post_id: str
    group: str
    mode: PublishMode
    targets: list[OutboundTarget]
    tags: list[str] = field(default_factory=list)
    short_links: bool = False


@dataclass
class PublishedTarget:
    target_id: str
    channel_id: str
    external_id: str
    external_group: str = ""


@dataclass
class ExternalPost:
    external_id: str
    channel_id: str
    provider: str = ""
    content: str = ""
    publish_date: datetime | None = None
    state: str = ""                # QUEUE | PUBLISHED | ERROR | DRAFT
    release_url: str = ""
    error: str = ""
    group: str = ""


@dataclass
class PostMetrics:
    metrics: dict[str, int]        # canonical names: impressions, reach, views, likes, comments, shares, saves, clicks, engagement
    raw: Any = None


@dataclass
class PublisherNotification:
    external_id: str
    created_at: datetime | None
    content: str
    level: str = "info"            # info | warning | error
    link: str = ""


@runtime_checkable
class Publisher(Protocol):
    name: str

    async def check_connection(self) -> dict[str, Any]: ...
    async def list_channels(self) -> list[PublisherChannel]: ...
    async def channel_rules(self, channel_id: str) -> ChannelRules: ...
    async def run_channel_tool(self, channel_id: str, method: str, data: dict[str, Any]) -> Any: ...
    async def find_slot(self, channel_id: str) -> datetime | None: ...

    async def upload_media(self, media: OutboundMedia) -> UploadedMedia: ...
    async def create_posts(self, post: OutboundPost) -> list[PublishedTarget]:
        """One create request. All targets must share the same `scheduled_at`."""
        ...
    async def find_existing_post(
        self, channel_id: str, scheduled_at: datetime, caption: str, *, window_minutes: int = 10,
    ) -> ExternalPost | None:
        """Find a post that an ambiguous create call may have produced (for reconciliation)."""
        ...
    async def list_posts(self, start: datetime, end: datetime) -> list[ExternalPost]: ...
    async def delete_post(self, external_id: str) -> None: ...
    async def set_post_status(self, external_id: str, status: Literal["draft", "schedule"]) -> None: ...
    async def update_post_settings(self, external_id: str, settings: dict[str, Any]) -> None: ...

    async def post_metrics(self, external_id: str, days: int = 7) -> PostMetrics: ...
    async def channel_metrics(self, channel_id: str, days: int = 30) -> list[dict[str, Any]]: ...
    async def notifications(self, page: int = 0) -> list[PublisherNotification]: ...
    async def generate_video(
        self, *, kind: str, prompt: str, orientation: str = "vertical",
        image_urls: list[str] | None = None, voice: str = "",
    ) -> list[UploadedMedia]: ...

    async def aclose(self) -> None: ...
