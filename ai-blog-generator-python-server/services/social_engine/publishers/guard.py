"""Live-publishing guard.

The development checkout and the live system run on the same machine and use
the same Postiz account. To make double-posting impossible, any call that
changes the Postiz account is refused unless the environment explicitly sets
`SOCIAL_ENGINE_LIVE_PUBLISHING=1` (only the live release's .env should).
Read-only calls (channels, rules, boards, posts, analytics) always work.
"""
from __future__ import annotations

import os
from typing import Any

from ..errors import ConfigurationError

LIVE_PUBLISHING_ENV = "SOCIAL_ENGINE_LIVE_PUBLISHING"

# Publisher methods that write to the Postiz account.
WRITE_METHODS = frozenset({
    "upload_media", "create_posts", "delete_post", "set_post_status", "update_post_settings",
    "generate_video", "upload_from_url", "delete_post_group", "update_release_id", "start_clipping",
})


def live_publishing_enabled() -> bool:
    return os.environ.get(LIVE_PUBLISHING_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


class ReadOnlyPublisher:
    """Wraps a publisher; write methods raise, everything else is delegated."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = f"{getattr(inner, 'name', 'publisher')} (read-only)"
        self.read_only = True

    def __getattr__(self, item: str) -> Any:
        attr = getattr(self._inner, item)
        if item in WRITE_METHODS:
            async def refused(*_args: Any, **_kwargs: Any) -> Any:
                raise ConfigurationError(
                    f"Live publishing is switched off in this environment, so '{item}' was not sent to Postiz. "
                    f"Only the live system should set {LIVE_PUBLISHING_ENV}=1 (it prevents dev and live "
                    "posting twice); use the dry-run publisher to rehearse here."
                )
            return refused
        return attr
