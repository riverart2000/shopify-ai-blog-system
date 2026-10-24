"""Analytics and debugging (activity, jobs, API calls, AI decisions, webhooks)."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db.social_engine import jobs as job_store, observability
from services.social_engine.analytics.reports import learned_weights, performance_report
from services.social_engine.errors import NotFoundError, ValidationError
from services.social_engine.jobs import load_handlers, registry
from services.social_engine.jobs.periodic import periodic_status
from services.social_engine.jobs.queue import enqueue

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])


@router.get("/analytics")
async def analytics(store_id: str = "", days: int = 30) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    days = max(1, min(days, 365))
    return {
        "report": await performance_report(sid, days=days),
        "learned": await learned_weights(sid),
        "insights": await observability.get_state(f"insights:{sid}"),
    }


@router.post("/analytics/collect")
async def analytics_collect(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    job_id, created = await enqueue("analytics.collect", {"reason": "manual"}, store_id=sid,
                                    dedupe_key=f"analytics.collect:manual:{sid}", priority=5)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


@router.post("/analytics/insights")
async def analytics_insights(store_id: str = "", days: int = 30) -> dict[str, Any]:
    """Ask Gemma to explain performance and suggest changes (numbers come from our data)."""
    from services.social_engine.ai.decisions import performance_insights
    from services.social_engine.context import load_store_context

    sid = await resolve_store_id(store_id)
    days = max(1, min(int(days), 365))
    report = await performance_report(sid, days=days)
    if not report.get("top_posts"):
        raise ValidationError("No metrics yet — insights need published posts with analytics.")
    rollups = {key: report[key][:15] for key in ("content_type", "template", "channel", "hour", "product")}
    rollups["totals"] = report["totals"]
    insights = await performance_insights(await load_store_context(sid), rollups=rollups)
    data = insights.model_dump() if hasattr(insights, "model_dump") else dict(insights)
    data["generated_at"] = int(time.time())
    await observability.set_state(f"insights:{sid}", data)
    return {"ok": True, "insights": data}


@router.get("/activity")
async def activity(
    store_id: str = "", entity_type: str = "", entity_id: str = "", level: str = "",
    limit: int = 100, before_id: int | None = None,
) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    return {"items": await observability.list_activity(
        store_id=sid, entity_type=entity_type, entity_id=entity_id, level=level, limit=limit, before_id=before_id,
    )}


@router.get("/jobs")
async def jobs(store_id: str = "", status: str = "", type: str = "", limit: int = 100) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    load_handlers()
    return {
        "jobs": await job_store.list_jobs(store_id=sid, status=status, job_type=type, limit=limit),
        "counts": await job_store.counts_by_status(sid),
        "types": {name: {"lane": spec.lane, "max_attempts": spec.max_attempts, "description": spec.description}
                  for name, spec in registry.all_types().items()},
        "periodic": await periodic_status(),
    }


class JobAction(BaseModel):
    store_id: str = ""


@router.post("/jobs/{job_id}/retry")
async def job_retry(job_id: str, payload: JobAction) -> dict[str, Any]:
    await resolve_store_id(payload.store_id)
    if not await job_store.retry(job_id):
        raise ValidationError("Only failed or cancelled jobs can be retried.")
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str, payload: JobAction) -> dict[str, Any]:
    await resolve_store_id(payload.store_id)
    if not await job_store.cancel(job_id):
        raise ValidationError("Only queued jobs can be cancelled.")
    return {"ok": True}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str) -> dict[str, Any]:
    job = await job_store.get(job_id)
    if not job:
        raise NotFoundError("Job not found.")
    return {
        "job": job,
        "timeline": await observability.list_activity(entity_type="job", entity_id=job_id, limit=50),
        "api_calls": await observability.list_api_calls(correlation_id=job.get("correlation_id") or job_id, limit=50),
    }


@router.get("/api-calls")
async def api_calls(service: str = "", errors: bool = False, hours: int = 24, limit: int = 100,
                    correlation_id: str = "") -> dict[str, Any]:
    since = int(time.time()) - max(1, min(hours, 24 * 30)) * 3600
    return {
        "calls": await observability.list_api_calls(service=service, only_errors=errors, since=since,
                                                    limit=limit, correlation_id=correlation_id),
        "stats": await observability.api_call_stats(since),
    }


@router.get("/decisions")
async def decisions(store_id: str = "", decision_type: str = "", subject_id: str = "", limit: int = 50) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    return {"decisions": await observability.list_decisions(
        store_id=sid, decision_type=decision_type, subject_id=subject_id, limit=limit)}


@router.get("/webhook-events")
async def webhook_events(source: str = "", limit: int = 50) -> dict[str, Any]:
    return {"events": await observability.list_webhooks(source=source, limit=limit)}
