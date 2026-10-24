"""Background worker: claims due jobs, runs them, and records every outcome.

Run it inside the scheduler process (production) or from the CLI:

    python -m services.social_engine.cli worker          # forever
    python -m services.social_engine.cli worker --once   # drain due jobs, then exit
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
import uuid

from db.social_engine import jobs as job_store
from db.social_engine import observability

from ..errors import Deferred, RateLimited, describe, is_retryable
from . import registry
from .registry import JobContext, JobType

logger = logging.getLogger("ai_blog_server.social_engine.worker")

HEARTBEAT_KEY_PREFIX = "worker:"
_MAINTENANCE_INTERVAL = 30.0


class Worker:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        poll_interval: float = 2.0,
        max_concurrency: int = 6,
        lane_limits: dict[str, int] | None = None,
        run_periodic: bool = True,
    ) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.poll_interval = poll_interval
        self.max_concurrency = max(1, max_concurrency)
        self.lane_limits = {**registry.LANE_LIMITS, **(lane_limits or {})}
        self.run_periodic = run_periodic
        self._tasks: set[asyncio.Task] = set()
        self._started_at = int(time.time())
        self._last_maintenance = 0.0
        self._processed = 0

    # ------------------------------------------------------------------ loop

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        from . import load_handlers

        load_handlers()
        stop = stop or asyncio.Event()
        logger.info("Social Engine worker %s started", self.worker_id)
        try:
            while not stop.is_set():
                try:
                    await self._maintenance_if_due()
                    await self._fill_capacity()
                except Exception:
                    logger.exception("Social Engine worker loop error (continuing)")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        finally:
            await self._shutdown()

    async def run_once(self, *, max_jobs: int = 100) -> int:
        """Run due jobs until none are left (or max_jobs). For CLI and tests."""
        from . import load_handlers

        load_handlers()
        await job_store.recover_expired_leases()
        started = 0
        while started < max_jobs:
            launched = await self._fill_capacity()
            if not launched and not self._tasks:
                break
            started += launched
            if self._tasks:
                await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        return started

    # -------------------------------------------------------------- claiming

    async def _full_lane_types(self) -> list[str]:
        excluded: list[str] = []
        for lane, limit in self.lane_limits.items():
            types = registry.types_in_lane(lane)
            if types and await job_store.count_running(types) >= limit:
                excluded.extend(types)
        return excluded

    async def _fill_capacity(self) -> int:
        launched = 0
        known_types = list(registry.all_types())
        if not known_types:
            return 0
        while len(self._tasks) < self.max_concurrency:
            excluded = await self._full_lane_types()
            allowed = [name for name in known_types if name not in excluded]
            if not allowed:
                break
            claimed = await job_store.claim_next(
                owner=self.worker_id, lease_seconds=60, allowed_types=allowed,
            )
            if not claimed:
                break
            spec = registry.get(claimed["type"])
            if spec is None:  # pragma: no cover - filtered by allowed_types
                await job_store.fail(claimed["id"], self.worker_id, error_type="UnknownJobType",
                                     error=f"No handler registered for {claimed['type']}")
                continue
            task = asyncio.create_task(self._execute(spec, claimed), name=f"job:{claimed['type']}:{claimed['id']}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            launched += 1
        return launched

    # ------------------------------------------------------------- execution

    async def _execute(self, spec: JobType, job: dict) -> None:
        job_id = str(job["id"])
        lease_seconds = spec.timeout_seconds + 120
        await job_store.extend_lease(job_id, self.worker_id, lease_seconds)

        async def extend() -> bool:
            return await job_store.extend_lease(job_id, self.worker_id, lease_seconds)

        ctx = JobContext(job=job, worker_id=self.worker_id, extend_lease=extend)
        heartbeat = asyncio.create_task(self._keep_lease(extend, lease_seconds))
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(spec.handler(ctx), timeout=spec.timeout_seconds)
            await job_store.complete(job_id, self.worker_id, result or {})
            self._processed += 1
            logger.debug("Job %s %s succeeded in %.1fs", spec.name, job_id, time.monotonic() - started)
        except Deferred as deferred:
            await job_store.reschedule(
                job_id, self.worker_id, run_at=deferred.run_at, error_type="Deferred",
                error=deferred.reason, consume_attempt=False,
            )
            await ctx.log(f"Deferred until {deferred.run_at}: {deferred.reason}", stage=spec.name)
        except RateLimited as limited:
            await job_store.reschedule(
                job_id, self.worker_id, run_at=limited.retry_at, error_type="RateLimited",
                error=limited.message, consume_attempt=False,
            )
            await ctx.log(f"Rate limited; retrying at {limited.retry_at}: {limited.message}",
                          level="warning", stage=spec.name)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(job_store.reschedule(
                    job_id, self.worker_id, run_at=int(time.time()) + 5, error_type="WorkerStopped",
                    error="Worker stopped while the job was running; requeued.", consume_attempt=False,
                ))
            raise
        except BaseException as exc:  # noqa: BLE001 - every failure must be recorded
            if isinstance(exc, asyncio.TimeoutError):
                error_type, message = "Timeout", f"Job exceeded its {spec.timeout_seconds}s time limit."
                retryable = True
            else:
                error_type, message = describe(exc)
                retryable = is_retryable(exc)
            attempts = int(job.get("attempts") or 1)
            max_attempts = int(job.get("max_attempts") or spec.max_attempts)
            if retryable and attempts < max_attempts:
                delay = spec.backoff(attempts)
                await job_store.reschedule(
                    job_id, self.worker_id, run_at=int(time.time()) + delay,
                    error_type=error_type, error=message,
                )
                await ctx.log(
                    f"Attempt {attempts}/{max_attempts} failed ({error_type}: {message}); retrying in {delay}s.",
                    level="warning", stage=spec.name,
                )
            else:
                await job_store.fail(job_id, self.worker_id, error_type=error_type, error=message)
                reason = "not retryable" if not retryable else f"{attempts} attempts used"
                await ctx.log(
                    f"Failed ({reason}) — {error_type}: {message}", level="error", stage=spec.name,
                    data={"job_type": spec.name, "payload": job.get("payload")},
                )
                await self._on_final_failure(spec, ctx, exc)
            if not isinstance(exc, Exception):
                raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _keep_lease(self, extend, lease_seconds: int) -> None:
        interval = max(10.0, lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await extend()

    async def _on_final_failure(self, spec: JobType, ctx: JobContext, exc: BaseException) -> None:
        hook = FAILURE_HOOKS.get(spec.name)
        if hook is None:
            return
        try:
            await hook(ctx, exc)
        except Exception:
            logger.exception("Failure hook for %s raised", spec.name)

    # ----------------------------------------------------------- maintenance

    async def _maintenance_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_maintenance < _MAINTENANCE_INTERVAL:
            return
        self._last_maintenance = now
        recovered = await job_store.recover_expired_leases()
        if recovered.get("failed") or recovered.get("requeued"):
            logger.warning(
                "Recovered expired job leases: %s requeued, %s failed",
                recovered.get("requeued", 0), recovered.get("failed", 0),
            )
        await observability.set_state(f"{HEARTBEAT_KEY_PREFIX}{self.worker_id}", {
            "worker_id": self.worker_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": self._started_at,
            "running": len(self._tasks),
            "processed": self._processed,
        })
        if self.run_periodic:
            from .periodic import schedule_due_periodic_jobs

            try:
                await schedule_due_periodic_jobs()
            except Exception:
                logger.exception("Periodic job scheduling failed")

    async def _shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Social Engine worker %s stopped", self.worker_id)


FAILURE_HOOKS: dict[str, object] = {}


def on_final_failure(job_type: str):
    """Register a coroutine run when a job of `job_type` fails permanently."""

    def decorator(func):
        FAILURE_HOOKS[job_type] = func
        return func

    return decorator


async def worker_health(max_age_seconds: int = 120) -> dict:
    states = await observability.list_state(HEARTBEAT_KEY_PREFIX)
    now = int(time.time())
    workers = [
        {**(item.get("value") or {}), "last_seen": item["updated_at"], "age_seconds": now - item["updated_at"]}
        for item in states.values()
    ]
    alive = [w for w in workers if w["age_seconds"] <= max_age_seconds]
    return {"alive": bool(alive), "workers": sorted(workers, key=lambda w: w["last_seen"], reverse=True)[:10]}
