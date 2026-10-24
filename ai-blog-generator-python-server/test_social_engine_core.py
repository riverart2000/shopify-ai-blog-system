"""Social Engine foundation: job queue/worker, resilient HTTP client, settings."""
from __future__ import annotations

import time

import httpx
import pytest

from db.social_engine import jobs as job_store
from db.social_engine import observability
from services.social_engine import http as se_http
from services.social_engine.errors import (
    ExternalServiceError, RateLimited, UncertainOutcome, ValidationError,
)
from services.social_engine.jobs import registry
from services.social_engine.jobs.queue import enqueue
from services.social_engine.jobs.worker import Worker, on_final_failure
from services.social_engine.settings import load_settings, save_settings, public_settings

CALLS: dict[str, int] = {}


@registry.job("test.ok", lane="default")
async def _ok(ctx):
    CALLS["ok"] = CALLS.get("ok", 0) + 1
    return {"echo": ctx.payload.get("value")}


@registry.job("test.flaky", lane="default", max_attempts=3, backoff_base=0)
async def _flaky(ctx):
    CALLS["flaky"] = CALLS.get("flaky", 0) + 1
    if ctx.attempt < 2:
        raise ExternalServiceError("postiz", "temporary", status_code=503)
    return {"attempt": ctx.attempt}


@registry.job("test.permanent", lane="default", max_attempts=5)
async def _permanent(ctx):
    raise ValidationError("caption is empty")


@registry.job("test.defer", lane="default")
async def _defer(ctx):
    ctx.defer(120, "waiting for media processing")


@registry.job("test.ratelimited", lane="postiz")
async def _limited(ctx):
    raise RateLimited("budget used", retry_at=int(time.time()) + 600)


HOOKED: list[str] = []


@on_final_failure("test.permanent")
async def _hook(ctx, exc):
    HOOKED.append(f"{ctx.id}:{type(exc).__name__}")


@pytest.mark.asyncio
async def test_worker_success_and_dedupe(se_db):
    job_id, created = await enqueue("test.ok", {"value": 7}, store_id=se_db, dedupe_key="ok-1")
    again, created_again = await enqueue("test.ok", {"value": 8}, store_id=se_db, dedupe_key="ok-1")
    assert created and not created_again and again == job_id
    assert await Worker(run_periodic=False).run_once() == 1
    job = await job_store.get(job_id)
    assert job["status"] == "succeeded" and job["result"] == {"echo": 7}


@pytest.mark.asyncio
async def test_worker_retries_retryable_errors_then_succeeds(se_db):
    job_id, _ = await enqueue("test.flaky", store_id=se_db)
    await Worker(run_periodic=False).run_once()  # backoff_base=0: the retry is due immediately
    job = await job_store.get(job_id)
    assert job["status"] == "succeeded" and job["attempts"] == 2
    timeline = await observability.list_activity(entity_type="job", entity_id=job_id)
    assert any("Attempt 1/3 failed" in row["message"] for row in timeline)


@pytest.mark.asyncio
async def test_worker_fails_permanent_errors_immediately_and_runs_hook(se_db):
    job_id, _ = await enqueue("test.permanent", store_id=se_db)
    await Worker(run_periodic=False).run_once()
    job = await job_store.get(job_id)
    assert job["status"] == "failed" and job["attempts"] == 1
    assert "caption is empty" in job["last_error"]
    assert HOOKED and HOOKED[-1].startswith(job_id)
    timeline = await observability.list_activity(entity_type="job", entity_id=job_id)
    assert timeline[0]["level"] == "error"


@pytest.mark.asyncio
async def test_defer_and_rate_limit_do_not_consume_attempts(se_db):
    deferred_id, _ = await enqueue("test.defer", store_id=se_db)
    limited_id, _ = await enqueue("test.ratelimited", store_id=se_db)
    await Worker(run_periodic=False).run_once()
    deferred = await job_store.get(deferred_id)
    limited = await job_store.get(limited_id)
    assert deferred["status"] == "queued" and deferred["attempts"] == 0
    assert deferred["run_at"] > time.time() + 60
    assert limited["status"] == "queued" and limited["attempts"] == 0
    assert limited["last_error_type"] == "RateLimited"


@pytest.mark.asyncio
async def test_expired_lease_is_recovered(se_db):
    job_id, _ = await enqueue("test.ok", store_id=se_db)
    claimed = await job_store.claim_next(owner="dead-worker", lease_seconds=-1)
    assert claimed["id"] == job_id
    result = await job_store.recover_expired_leases()
    assert result["requeued"] == 1
    assert (await job_store.get(job_id))["status"] == "queued"


@pytest.mark.asyncio
async def test_unknown_job_type_is_rejected(se_db):
    with pytest.raises(ValueError):
        await enqueue("does.not.exist")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _transport(responses: list):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_idempotent_request_retries_5xx_and_records_each_attempt(se_db):
    transport, calls = _transport([httpx.Response(503, text="busy"), httpx.Response(200, json={"ok": True})])
    client = se_http.ServiceClient("postiz", "https://api.test", transport=transport, backoff_base=0.001)
    body = await client.json("GET", "/integrations", operation="integrations.list",
                             ctx=se_http.CallContext(store_id=se_db, correlation_id="c1"))
    await client.aclose()
    assert body == {"ok": True} and calls["n"] == 2
    ledger = await observability.list_api_calls(correlation_id="c1")
    assert [row["status_code"] for row in ledger] == [200, 503]


@pytest.mark.asyncio
async def test_create_post_server_error_is_uncertain_not_retried(se_db):
    transport, calls = _transport([httpx.Response(500, text="oops")])
    client = se_http.ServiceClient("postiz", "https://api.test", transport=transport, backoff_base=0.001)
    with pytest.raises(UncertainOutcome):
        await client.request("POST", "/posts", operation="posts.create", json={"a": 1})
    await client.aclose()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_create_post_connect_error_is_retried_because_nothing_was_sent(se_db):
    transport, calls = _transport([httpx.ConnectError("refused"), httpx.Response(200, json=[{"postId": "p1"}])])
    client = se_http.ServiceClient("postiz", "https://api.test", transport=transport, backoff_base=0.001)
    body = await client.json("POST", "/posts", operation="posts.create", json={"a": 1})
    await client.aclose()
    assert body == [{"postId": "p1"}] and calls["n"] == 2


@pytest.mark.asyncio
async def test_429_on_post_raises_rate_limited_with_retry_after(se_db):
    transport, _ = _transport([httpx.Response(429, headers={"retry-after": "120"}, text="slow down")])
    client = se_http.ServiceClient("postiz", "https://api.test", transport=transport)
    with pytest.raises(RateLimited) as info:
        await client.request("POST", "/posts", operation="posts.create", json={})
    await client.aclose()
    assert 100 < info.value.retry_at - time.time() <= 121


@pytest.mark.asyncio
async def test_4xx_is_not_retried_and_not_retryable(se_db):
    transport, calls = _transport([httpx.Response(400, json={"message": "bad board"})])
    client = se_http.ServiceClient("postiz", "https://api.test", transport=transport)
    with pytest.raises(ExternalServiceError) as info:
        await client.request("GET", "/x", operation="x.get")
    await client.aclose()
    assert calls["n"] == 1 and info.value.retryable is False and "bad board" in info.value.message


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_defaults_merge_and_validation(se_db):
    settings = await load_settings(se_db)
    assert settings.brand.business_name == "BioluxeLab"
    assert settings.brand.website == "https://bioluxelab.com"
    assert settings.ai.model.startswith("gemma")
    updated = await save_settings(se_db, {"captions": {"tone": "friendly"}, "brand": {"colors": {"primary": "#112233"}}})
    assert updated.captions.tone == "friendly" and updated.brand.colors.primary == "#112233"
    assert updated.brand.colors.text == "#15251c"  # untouched sibling kept
    with pytest.raises(Exception):
        await save_settings(se_db, {"brand": {"colors": {"primary": "green"}}})
    assert (await load_settings(se_db)).brand.colors.primary == "#112233"
    public = public_settings(updated)
    assert "webhook_token" not in public["postiz"]
