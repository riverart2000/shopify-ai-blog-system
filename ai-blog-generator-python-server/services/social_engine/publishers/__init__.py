"""Social Engine publishers: Postiz (production) and dry-run.

    publisher = get_publisher(sctx, correlation_id=job_id)
    try:
        published = await publisher.create_posts(post)
    finally:
        await publisher.aclose()

`base.py` holds the contract; `postiz_client.py` is the raw API client;
`postiz_settings.py` builds the per-network `settings`; `webhook.py` parses
Postiz webhook deliveries.
"""
from __future__ import annotations

from ..context import StoreContext
from .base import (
    ChannelRules, ExternalPost, OutboundMedia, OutboundPost, OutboundTarget, PostMetrics, PublishedTarget,
    Publisher, PublisherChannel, PublisherNotification, PublishMode, UploadedMedia,
)
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
    publisher = PostizPublisher(sctx, ctx)
    if not live_publishing_enabled():
        return ReadOnlyPublisher(publisher)  # type: ignore[return-value]
    return publisher


__all__ = [
    "CANONICAL_METRICS", "ChannelRules", "DRY_RUN_CHANNELS", "DryRunPublisher", "ExternalPost",
    "OutboundMedia", "OutboundPost", "OutboundTarget", "PartialPublishError", "PostMetrics",
    "PostizClient", "PostizPublisher", "PublishMode", "PublishedTarget", "Publisher",
    "PublisherChannel", "PublisherNotification", "SUPPORTED_PROVIDERS", "UploadedMedia",
    "build_settings", "canonical_metric", "caption_matches", "classify_notification",
    "extract_post_ids", "get_publisher", "map_channel_metrics", "map_post_metrics",
    "normalise_caption", "payload_fingerprint", "target_group",
]
