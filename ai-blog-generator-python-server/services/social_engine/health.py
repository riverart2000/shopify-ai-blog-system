"""Doctor: one call that checks every dependency and explains what's wrong.

Used by the Overview page, `GET /api/social-engine/health` and
`python -m services.social_engine.cli doctor`.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from db.social_engine import config_store, jobs as job_store, observability
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from db.social_engine.connection import connect
from db.social_engine.schema import LATEST_VERSION

from .context import StoreContext, load_store_context
from .jobs.worker import worker_health

Check = dict[str, Any]


def _check(name: str, status: str, message: str, **details: Any) -> Check:
    return {"name": name, "status": status, "message": message, "details": details}


async def _database() -> Check:
    async with connect() as conn:
        async with conn.execute("SELECT value FROM social_engine_meta WHERE key='schema_version'") as cur:
            row = await cur.fetchone()
        async with conn.execute("PRAGMA journal_mode") as cur:
            mode = (await cur.fetchone())[0]
    version = int(row[0]) if row else 0
    if version != LATEST_VERSION:
        return _check("database", "error", f"Schema v{version}, expected v{LATEST_VERSION}. Restart the backend to migrate.")
    return _check("database", "ok", f"SQLite schema v{version} ({mode} mode)")


async def _worker() -> Check:
    health = await worker_health()
    counts = await job_store.counts_by_status()
    queued = await job_store.list_jobs(status="queued", limit=500)
    now = int(time.time())
    overdue = [j for j in queued if j["run_at"] < now - 600]
    if not health["alive"]:
        return _check("worker", "error",
                      "No Social Engine worker is running — jobs will not be processed. "
                      "It runs inside the scheduler process (or `python -m services.social_engine.cli worker`).",
                      jobs=counts)
    if overdue:
        return _check("worker", "warning", f"{len(overdue)} job(s) overdue by more than 10 minutes.",
                      jobs=counts, workers=health["workers"])
    return _check("worker", "ok", f"Worker alive; {counts.get('queued', 0)} queued, {counts.get('running', 0)} running",
                  jobs=counts, workers=health["workers"])


async def _postiz(sctx: StoreContext, live: bool) -> Check:
    settings = sctx.settings
    if settings.publisher == "dry_run":
        return _check("postiz", "warning", "Dry-run mode: posts are NOT sent to Postiz.")
    if not settings.postiz.api_key():
        return _check("postiz", "error",
                      f"Postiz API key missing. Add {settings.postiz.api_key_env}=<key> to the server .env "
                      "(Postiz → Settings → Developers → Public API) and restart.")
    from .publishers.guard import LIVE_PUBLISHING_ENV, live_publishing_enabled

    read_only = not live_publishing_enabled()
    since = int(time.time()) - 3600
    used = await observability.count_api_calls(service="postiz", operation="posts.create", since=since)
    budget = settings.postiz.create_post_hourly_budget
    if read_only and not live:
        return _check("postiz", "warning",
                      f"Read-only: this environment can't publish ({LIVE_PUBLISHING_ENV} is not set). "
                      "Only the live system should publish.")
    if not live:
        return _check("postiz", "ok", f"API key configured; {used}/{budget} create calls used this hour")
    from .publishers import get_publisher

    publisher = get_publisher(sctx, correlation_id="doctor")
    try:
        info = await publisher.check_connection()
        channels = await publisher.list_channels()
    except Exception as exc:
        return _check("postiz", "error", f"Postiz unreachable or key rejected: {exc}")
    finally:
        await publisher.aclose()
    if not info.get("connected", True):
        return _check("postiz", "error", "Postiz says the API key is not connected.", info=info)
    disabled = [c.name for c in channels if c.disabled]
    status = "warning" if disabled or not channels else "ok"
    message = f"Connected; {len(channels)} channel(s); {used}/{budget} create calls used this hour"
    if disabled:
        message += f"; needs reconnecting: {', '.join(disabled)}"
    if not channels:
        message = "Connected, but no channels are connected in Postiz yet."
    if read_only:
        status = "warning"
        message += f" — READ-ONLY here: set {LIVE_PUBLISHING_ENV}=1 only on the live system to publish."
    return _check("postiz", status, message, channels=len(channels), read_only=read_only)


async def _gemma(sctx: StoreContext, live: bool) -> Check:
    ai = sctx.settings.ai
    if not live:
        return _check("gemma", "ok", f"{ai.model} at {ai.base_url} (not tested)")
    try:
        from .ai.llm import check_llm

        result = await check_llm(ai)
    except Exception as exc:
        return _check("gemma", "error", f"Gemma at {ai.base_url} failed: {type(exc).__name__}: {exc}. "
                      "Is the oMLX app running?")
    ok = result.get("ok", result.get("status") == "ok")
    # The LLM result has its own name/status/message keys; keep them out of the check's details.
    details = {k: v for k, v in result.items() if k not in ("name", "status", "message")}
    return _check("gemma", "ok" if ok else "error", result.get("message") or
                  f"{ai.model} responded in {result.get('latency_ms', '?')} ms", **details)


async def _ffmpeg() -> Check:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        return _check("ffmpeg", "error", f"Missing {', '.join(missing)} — product videos can't be rendered. "
                      "Install with `brew install ffmpeg`.")
    proc = await asyncio.create_subprocess_exec("ffmpeg", "-version", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return _check("ffmpeg", "ok", out.decode(errors="replace").splitlines()[0][:80])


async def _storage() -> Check:
    try:
        from .creatives.storage import media_root

        root = Path(media_root())
    except Exception:
        root = Path(os.environ.get("SOCIAL_ENGINE_MEDIA_ROOT") or Path(__file__).resolve().parents[2] / "data/social_engine/media")
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write-test"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return _check("media_storage", "error", f"Media folder {root} is not writable: {exc}")
    free_gb = shutil.disk_usage(root).free / 1e9
    status = "warning" if free_gb < 5 else "ok"
    return _check("media_storage", status, f"{root} writable; {free_gb:.1f} GB free")


async def _shopify(sctx: StoreContext, live: bool) -> Check:
    counts = await product_store.count_products(sctx.store_id)
    cursor, synced_at = await observability.get_state_with_time(f"catalog:cursor:{sctx.store_id}")
    if not counts.get("total"):
        message = "No products synced yet — run 'Sync catalogue'."
        status = "warning"
    else:
        age_h = (time.time() - (counts.get("last_synced_at") or 0)) / 3600
        status = "warning" if age_h > 24 else "ok"
        message = (f"{counts['total']} products ({counts.get('available', 0)} in stock, "
                   f"{counts.get('on_sale', 0)} on sale); last sync {age_h:.1f}h ago")
    if live:
        import shopify_client

        try:
            await shopify_client.graphql_request(sctx.store_config, "{ shop { name } }")
        except Exception as exc:
            return _check("shopify", "error", f"Shopify Admin API failed: {exc}")
    return _check("shopify", status, message, cursor=cursor)


async def _channels(sctx: StoreContext) -> Check:
    channels = await config_store.list_channels(sctx.store_id)
    usable = [c for c in channels if c["enabled"] and not c["disabled"] and not c.get("missing_at")]
    problems = []
    for channel in usable:
        if channel["provider"] == "pinterest" and not (channel.get("default_settings") or {}).get("board"):
            problems.append(f"{channel['name']}: choose a Pinterest board")
    if not channels:
        return _check("channels", "warning", "No channels synced yet — connect channels in Postiz, then 'Sync channels'.")
    if problems:
        return _check("channels", "warning", "; ".join(problems), usable=len(usable))
    return _check("channels", "ok", f"{len(usable)} of {len(channels)} channel(s) enabled for posting")


async def _recent_problems(sctx: StoreContext) -> Check:
    posts = await post_store.count_posts_by_status(sctx.store_id)
    failed_jobs = (await job_store.counts_by_status(sctx.store_id)).get("failed", 0)
    errors = await observability.list_activity(store_id=sctx.store_id, level="error", limit=50)
    day_errors = [e for e in errors if e["created_at"] > time.time() - 86400]
    status = "warning" if posts.get("failed") or day_errors else "ok"
    return _check("recent_problems", status,
                  f"{posts.get('failed', 0)} failed post(s), {failed_jobs} failed job(s), "
                  f"{len(day_errors)} error(s) in the last 24h",
                  pending_approval=posts.get("pending_approval", 0))


async def _daily_fill(sctx: StoreContext) -> Check:
    from .scheduling.fill import daily_fill_status

    active = [c for c in await config_store.list_campaigns(sctx.store_id, statuses=("active",))
              if c["rule_type"] == "smartq"]
    if not active:
        return _check("daily_fill", "warning" if sctx.settings.enabled else "skipped",
                      "No active SmartQ campaign — daily slots won't be filled automatically.")
    days = await daily_fill_status(sctx.store_id, days=2)
    tomorrow = days[1]
    short = [f"{c['name']} ({c['provider']}) {c['planned']}/{c['target']}" for c in tomorrow["channels"] if not c["full"]]
    if short:
        return _check("daily_fill", "warning",
                      f"Tomorrow is not fully planned yet: {', '.join(short)}. SmartQ fills it hourly; "
                      "persistent gaps usually mean no eligible products or a channel limit.",
                      days=days)
    return _check("daily_fill", "ok",
                  f"Tomorrow fully planned ({tomorrow['filled']}/{tomorrow['slots']} slots across channels)", days=days)


async def _webhooks(sctx: StoreContext) -> Check:
    if not sctx.settings.postiz.webhook_token:
        return _check("webhooks", "warning", "Postiz webhook not set up yet (optional; status is also polled every 10 min).")
    received = await observability.list_webhooks(source="postiz", limit=1)
    last = received[0]["received_at"] if received else None
    return _check("webhooks", "ok", "Webhook token set" + (f"; last delivery {int((time.time() - last) / 60)} min ago" if last else "; no deliveries yet"))


async def run_doctor(store_id: str, *, live: bool = True) -> dict[str, Any]:
    sctx = await load_store_context(store_id)
    checks: list[tuple[str, Callable[[], Awaitable[Check]]]] = [
        ("database", _database), ("worker", _worker), ("postiz", lambda: _postiz(sctx, live)),
        ("gemma", lambda: _gemma(sctx, live)), ("ffmpeg", _ffmpeg), ("media_storage", _storage),
        ("shopify", lambda: _shopify(sctx, live)), ("channels", lambda: _channels(sctx)),
        ("daily_fill", lambda: _daily_fill(sctx)), ("webhooks", lambda: _webhooks(sctx)),
        ("recent_problems", lambda: _recent_problems(sctx)),
    ]
    results: list[Check] = []
    for name, check in checks:
        try:
            results.append(await check())
        except Exception as exc:  # a broken check is itself a finding
            results.append(_check(name, "error", f"{type(exc).__name__}: {exc}"))
    summary = "error" if any(r["status"] == "error" for r in results) else \
        "warning" if any(r["status"] == "warning" for r in results) else "ok"
    report = {"store_id": store_id, "status": summary, "checked_at": int(time.time()), "live": live,
              "engine_enabled": sctx.settings.enabled, "publisher": sctx.settings.publisher, "checks": results}
    if live:
        await observability.set_state(f"doctor:{store_id}", report)
    return report
