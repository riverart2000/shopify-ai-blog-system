"""Social Engine publishers: Postiz (production, one main server plus optional extra ones) and dry-run.

    publisher = get_publisher(sctx, correlation_id=job_id)
    try:
        published = await publisher.create_posts(post)
    finally:
        await publisher.aclose()

`base.py` holds the contract; `postiz_client.py` is the raw API client;
`postiz_settings.py` builds the per-network `settings`; `webhook.py` parses
Postiz webhook deliveries; `combined.py` puts extra Postiz servers
(`settings.postiz.servers`) behind the same publisher.
"""
from __future__ import annotations

from ..context import StoreContext
from .base import (
    ChannelRules, ExternalPost, OutboundMedia, OutboundPost, OutboundTarget, PostMetrics, PublishedTarget,
    Publisher, PublisherChannel, PublisherNotification, PublishMode, UploadedMedia,
)
from .combined import CombinedPublisher, server_name, server_settings
from .common import caption_matches, normalise_caption, target_group
from .dry_run import DRY_RUN_CHANNELS, DryRunPublisher
from .guard import LIVE_PUBLISHING_ENV, ReadOnlyPublisher, live_publishing_enabled
from .postiz import (
    CANONICAL_METRICS, PartialPublishError, PostizPublisher, canonical_metric, classify_notification,
    map_channel_metrics, map_post_metrics,
)
from .postiz_client import PostizClient
from .postiz_settings import SUPPORTED_PROVIDERS, build_settings
from .webhook import extract_post_ids, payload_fingerprint


def get_publisher(sctx: StoreContext, *, correlation_id: str = "") -> Publisher:
    """The publisher configured for this store (`settings.publisher`).

    Raises `ConfigurationError` when Postiz is selected but no API key is set.
    """
    ctx = sctx.call_context(correlation_id)
    if sctx.settings.publisher == "dry_run":
        return DryRunPublisher(sctx, ctx)
    publisher: Publisher = PostizPublisher(sctx, ctx)
    extras: dict[str, PostizPublisher] = {}
    problems: dict[str, str] = {}
    for server in sctx.settings.postiz.servers:
        if not server.enabled:
            continue
        if not server.api_key():
            # The main Postiz keeps working; only this server's channels are refused, with this reason.
            problems[server.name] = f"its API key is missing (add {server.api_key_env}=<key> to the server .env)"
            continue
        extras[server.name] = PostizPublisher(
            sctx, ctx, settings=server_settings(sctx.settings.postiz, server), service=f"postiz:{server.name}",
        )
    if extras or problems:
        publisher = CombinedPublisher(publisher, extras, problems=problems)  # type: ignore[assignment]
    if not live_publishing_enabled():
        return ReadOnlyPublisher(publisher)  # type: ignore[return-value]
    return publisher


__all__ = [
    "CANONICAL_METRICS", "ChannelRules", "CombinedPublisher", "DRY_RUN_CHANNELS", "DryRunPublisher", "ExternalPost",
    "OutboundMedia", "OutboundPost", "OutboundTarget", "PartialPublishError", "PostMetrics",
    "PostizClient", "PostizPublisher", "PublishMode", "PublishedTarget", "Publisher",
    "PublisherChannel", "PublisherNotification", "SUPPORTED_PROVIDERS", "UploadedMedia",
    "build_settings", "canonical_metric", "caption_matches", "classify_notification",
    "extract_post_ids", "get_publisher", "map_channel_metrics", "map_post_metrics",
    "normalise_caption", "payload_fingerprint", "server_name", "target_group",
]
