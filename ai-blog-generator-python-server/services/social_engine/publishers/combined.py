"""The main Postiz plus extra Postiz servers behind one publisher.

Extra servers are listed in `settings.postiz.servers` (name, address, the .env variable that holds its API key).
Every channel and post on an extra server carries the server's name in its id ("local:cm..."), in every id the
engine stores, so each call goes to the right server without a lookup. Ids on the main server have no prefix
(Postiz ids never contain ":").

Pictures and videos are always uploaded to the main server. An extra server on a private address can't hand
files to the networks (Postiz refuses to fetch private addresses), but it does accept the main server's public
file address, so the same upload serves both.

An extra server that is down never stops the main one: its channels are left out of the channel list, its posts
are left alone by the reconciler (`unreachable`), and only calls for its own channels fail.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from ..errors import ConfigurationError, SocialEngineError, ValidationError
from .base import (
    ChannelRules, ExternalPost, OutboundMedia, OutboundPost, PostMetrics, PublishedTarget, PublisherChannel,
    PublisherNotification, UploadedMedia,
)

logger = logging.getLogger("ai_blog_server.social_engine.publishers")

SEPARATOR = ":"


def server_name(value: str) -> str:
    """The extra server a channel or post id belongs to, or "" for the main Postiz."""
    name, found, _ = (value or "").partition(SEPARATOR)
    return name if found else ""


def with_server(name: str, value: str) -> str:
    return f"{name}{SEPARATOR}{value}" if name and value else value


def without_server(value: str) -> str:
    return (value or "").partition(SEPARATOR)[2] if server_name(value) else (value or "")


def server_settings(postiz: Any, server: Any) -> Any:
    """The main Postiz settings with this server's address, key variable and budget (and no customer filter)."""
    return postiz.model_copy(update={
        "base_url": server.base_url, "api_key_env": server.api_key_env,
        "create_post_hourly_budget": server.create_post_hourly_budget, "customer_id": "", "servers": [],
    })


class CombinedPublisher:
    name = "postiz"

    def __init__(self, main: Any, extras: dict[str, Any], *, problems: dict[str, str] | None = None) -> None:
        self.main = main
        self.extras = dict(extras)
        self.problems = dict(problems or {})   # servers that are switched on but can't be used, with the reason
        self.unreachable: dict[str, str] = {}  # servers that failed during this job, with the reason

    def __getattr__(self, item: str) -> Any:
        # Postiz extras (AI video, clipping, the media library, upload-from-url) exist on the main server only.
        return getattr(self.main, item)

    # -- routing ---------------------------------------------------------------

    def _for(self, value: str) -> tuple[str, Any, str]:
        """(server name, its publisher, the id as that server knows it)."""
        name = server_name(value)
        if not name:
            return "", self.main, value
        publisher = self.extras.get(name)
        if publisher is None:
            reason = self.problems.get(name) or "it is switched off or no longer listed in the Postiz settings"
            raise ConfigurationError(f"'{value}' belongs to the Postiz server '{name}', which can't be used: {reason}.",
                                     details={"server": name})
        return name, publisher, without_server(value)

    def _failed(self, name: str, what: str, exc: SocialEngineError) -> None:
        self.unreachable[name] = exc.message
        logger.warning("Postiz server '%s' failed (%s): %s", name, what, exc.message,
                       extra={"operation": "social_engine.publishers.combined"})

    # -- account & channels ----------------------------------------------------

    async def check_connection(self) -> dict[str, Any]:
        result = await self.main.check_connection()
        servers: dict[str, Any] = {name: {"connected": False, "error": reason} for name, reason in self.problems.items()}
        for name, publisher in self.extras.items():
            try:
                servers[name] = await publisher.check_connection()
            except SocialEngineError as exc:
                servers[name] = {"connected": False, "error": exc.message}
        result["servers"] = servers
        return result

    async def list_groups(self) -> list[dict[str, Any]]:
        return await self.main.list_groups()   # groups are the main Postiz's; extra servers have none

    async def list_channels(self) -> list[PublisherChannel]:
        channels = list(await self.main.list_channels())
        for name, publisher in self.extras.items():
            try:
                found = await publisher.list_channels()
            except SocialEngineError as exc:
                self._failed(name, "channels", exc)
                continue
            channels.extend(replace(channel, channel_id=with_server(name, channel.channel_id)) for channel in found)
        return channels

    async def channel_rules(self, channel_id: str) -> ChannelRules:
        _, publisher, raw = self._for(channel_id)
        return await publisher.channel_rules(raw)

    async def run_channel_tool(self, channel_id: str, method: str, data: dict[str, Any]) -> Any:
        _, publisher, raw = self._for(channel_id)
        return await publisher.run_channel_tool(raw, method, data)

    async def find_slot(self, channel_id: str) -> datetime | None:
        _, publisher, raw = self._for(channel_id)
        return await publisher.find_slot(raw)

    async def channel_metrics(self, channel_id: str, days: int = 30) -> list[dict[str, Any]]:
        _, publisher, raw = self._for(channel_id)
        return await publisher.channel_metrics(raw, days)

    # -- media -----------------------------------------------------------------

    async def upload_media(self, media: OutboundMedia) -> UploadedMedia:
        return await self.main.upload_media(media)

    # -- posts -----------------------------------------------------------------

    async def create_posts(self, post: OutboundPost) -> list[PublishedTarget]:
        names = {server_name(target.channel_id) for target in post.targets}
        if len(names) > 1:
            raise ValidationError(
                f"Post {post.post_id}: one create request goes to one Postiz server, but these channels are on "
                f"{len(names)}. Split the targets by server.", details={"post_id": post.post_id},
            )
        name = next(iter(names), "")
        if not name:
            return await self.main.create_posts(post)
        _, publisher, _ = self._for(post.targets[0].channel_id)
        request = replace(post, targets=[
            replace(target, channel_id=without_server(target.channel_id)) for target in post.targets
        ])
        try:
            published = await publisher.create_posts(request)
        except SocialEngineError as exc:
            # The caller stores what the error reports (ids of the targets that did get created).
            for key in ("published", "targets"):
                for item in exc.details.get(key) or []:
                    if isinstance(item, dict):
                        for field in ("channel_id", "external_id"):
                            if item.get(field):
                                item[field] = with_server(name, str(item[field]))
            raise
        return [replace(item, channel_id=with_server(name, item.channel_id),
                        external_id=with_server(name, item.external_id)) for item in published]

    def _external(self, name: str, post: ExternalPost) -> ExternalPost:
        return replace(post, external_id=with_server(name, post.external_id),
                       channel_id=with_server(name, post.channel_id)) if name else post

    async def find_existing_post(
        self, channel_id: str, scheduled_at: datetime, caption: str, *, window_minutes: int = 10,
    ) -> ExternalPost | None:
        name, publisher, raw = self._for(channel_id)
        found = await publisher.find_existing_post(raw, scheduled_at, caption, window_minutes=window_minutes)
        return self._external(name, found) if found else None

    async def list_posts(self, start: datetime, end: datetime) -> list[ExternalPost]:
        """Posts on every server that answers. A server that doesn't is named in `unreachable`: its posts are
        unknown, not gone, so the caller must leave them alone."""
        posts = list(await self.main.list_posts(start, end))
        for name, publisher in self.extras.items():
            try:
                found = await publisher.list_posts(start, end)
            except SocialEngineError as exc:
                self._failed(name, "posts", exc)
                continue
            posts.extend(self._external(name, post) for post in found)
        return posts

    async def delete_post(self, external_id: str) -> None:
        _, publisher, raw = self._for(external_id)
        await publisher.delete_post(raw)

    async def set_post_status(self, external_id: str, status: Literal["draft", "schedule"]) -> None:
        _, publisher, raw = self._for(external_id)
        await publisher.set_post_status(raw, status)

    async def update_post_settings(self, external_id: str, settings: dict[str, Any]) -> None:
        _, publisher, raw = self._for(external_id)
        await publisher.update_post_settings(raw, settings)

    async def missing_content(self, external_id: str) -> list[dict[str, Any]]:
        _, publisher, raw = self._for(external_id)
        return await publisher.missing_content(raw)

    async def update_release_id(self, external_id: str, release_id: str) -> dict[str, Any]:
        _, publisher, raw = self._for(external_id)
        return await publisher.update_release_id(raw, release_id)

    # -- analytics & notifications ---------------------------------------------

    async def post_metrics(self, external_id: str, days: int = 7) -> PostMetrics:
        _, publisher, raw = self._for(external_id)
        return await publisher.post_metrics(raw, days)

    async def notifications(self, page: int = 0) -> list[PublisherNotification]:
        items = list(await self.main.notifications(page=page))
        for name, publisher in self.extras.items():
            try:
                found = await publisher.notifications(page=page)
            except SocialEngineError as exc:
                self._failed(name, "notifications", exc)
                continue
            items.extend(replace(item, external_id=with_server(name, item.external_id)) for item in found)
        return items

    async def aclose(self) -> None:
        for publisher in (self.main, *self.extras.values()):
            await publisher.aclose()


__all__ = ["CombinedPublisher", "SEPARATOR", "server_name", "server_settings", "with_server", "without_server"]
