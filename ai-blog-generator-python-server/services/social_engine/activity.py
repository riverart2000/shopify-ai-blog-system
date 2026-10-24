"""One call to record what happened: timeline row + structured log line.

Warnings and errors also reach System Health automatically through the
existing logging handler (it captures WARNING+ with store/correlation ids).
"""
from __future__ import annotations

import logging
from typing import Any

from db.social_engine import observability

logger = logging.getLogger("ai_blog_server.social_engine")

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


async def record(
    store_id: str,
    entity_type: str,
    entity_id: str,
    message: str,
    *,
    level: str = "info",
    stage: str = "",
    data: dict[str, Any] | None = None,
    correlation_id: str = "",
    log: bool = True,
) -> None:
    level = level if level in _LEVELS else "info"
    try:
        await observability.add_activity(
            store_id=store_id, entity_type=entity_type, entity_id=entity_id, message=message,
            level=level, stage=stage, data=data, correlation_id=correlation_id or entity_id,
        )
    except Exception:
        logger.debug("Could not write activity row", exc_info=True)
    if log:
        logger.log(
            _LEVELS[level],
            "[%s %s] %s%s",
            entity_type, entity_id, f"{stage}: " if stage else "", message,
            extra={
                "operation": f"social_engine.{stage or entity_type}",
                "store_id": store_id,
                "correlation_id": correlation_id or entity_id,
            },
        )
