"""Job type registry.

Register a handler with the decorator:

    @job("post.generate", lane="llm", max_attempts=3, timeout_seconds=900)
    async def generate_post(ctx: JobContext) -> dict: ...

Lanes cap how many jobs of a kind run at once across all worker processes
(e.g. the local Gemma server serves one request at a time, so "llm" = 1).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..activity import record
from ..errors import Deferred

LANE_LIMITS: dict[str, int] = {
    "default": 4,
    "llm": 1,
    "render": 2,
    "postiz": 1,
    "shopify": 1,
}


@dataclass
class JobContext:
    job: dict
    worker_id: str
    extend_lease: Callable[[], Awaitable[bool]] = field(repr=False)

    @property
    def id(self) -> str:
        return str(self.job["id"])

    @property
    def store_id(self) -> str:
        return str(self.job.get("store_id") or "")

    @property
    def payload(self) -> dict:
        return dict(self.job.get("payload") or {})

    @property
    def correlation_id(self) -> str:
        return str(self.job.get("correlation_id") or self.job["id"])

    @property
    def attempt(self) -> int:
        return int(self.job.get("attempts") or 1)

    @property
    def max_attempts(self) -> int:
        return int(self.job.get("max_attempts") or 1)

    async def log(
        self,
        message: str,
        *,
        level: str = "info",
        stage: str = "",
        data: dict[str, Any] | None = None,
        entity_type: str = "job",
        entity_id: str = "",
    ) -> None:
        await record(
            self.store_id, entity_type, entity_id or self.id, message, level=level,
            stage=stage or str(self.job.get("type")), data=data, correlation_id=self.correlation_id,
        )

    async def heartbeat(self) -> None:
        await self.extend_lease()

    def defer(self, seconds: int, reason: str) -> None:
        raise Deferred(reason, run_at=int(time.time()) + max(1, int(seconds)))


Handler = Callable[[JobContext], Awaitable[dict | None]]


@dataclass(frozen=True)
class JobType:
    name: str
    handler: Handler
    lane: str = "default"
    max_attempts: int = 3
    timeout_seconds: int = 600
    backoff_base: int = 30
    backoff_max: int = 3600
    description: str = ""

    def backoff(self, attempt: int) -> int:
        return int(min(self.backoff_max, self.backoff_base * (2 ** max(0, attempt - 1))))


_REGISTRY: dict[str, JobType] = {}


def job(
    name: str,
    *,
    lane: str = "default",
    max_attempts: int = 3,
    timeout_seconds: int = 600,
    backoff_base: int = 30,
    backoff_max: int = 3600,
    description: str = "",
) -> Callable[[Handler], Handler]:
    if lane not in LANE_LIMITS:
        raise ValueError(f"Unknown job lane {lane!r}; add it to LANE_LIMITS")

    def decorator(func: Handler) -> Handler:
        doc_lines = (func.__doc__ or "").strip().splitlines()
        _REGISTRY[name] = JobType(
            name=name, handler=func, lane=lane, max_attempts=max_attempts,
            timeout_seconds=timeout_seconds, backoff_base=backoff_base, backoff_max=backoff_max,
            description=description or (doc_lines[0] if doc_lines else ""),
        )
        return func

    return decorator


def get(name: str) -> JobType | None:
    return _REGISTRY.get(name)


def all_types() -> dict[str, JobType]:
    return dict(_REGISTRY)


def types_in_lane(lane: str) -> list[str]:
    return [name for name, spec in _REGISTRY.items() if spec.lane == lane]
