"""Social Engine command line — for debugging and operations on the server.

Run from ai-blog-generator-python-server/:

    ../.venv/bin/python -m services.social_engine.cli doctor
    ../.venv/bin/python -m services.social_engine.cli post <post_id>        # full story of one post
    ../.venv/bin/python -m services.social_engine.cli jobs --status failed
    ../.venv/bin/python -m services.social_engine.cli job-retry <job_id>
    ../.venv/bin/python -m services.social_engine.cli evaluate --campaign <id> --dry-run
    ../.venv/bin/python -m services.social_engine.cli worker --once
    ../.venv/bin/python -m services.social_engine.cli calls --service postiz --errors

`--store` defaults to AI_BLOG_BACKEND_STORE_ID or the first store.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_SERVER_DIR = Path(__file__).resolve().parents[2]
for _env in (_SERVER_DIR / ".env", _SERVER_DIR.parent / ".env"):
    if _env.exists():
        load_dotenv(_env, override=False)
        break


def _fmt_time(ts: Any) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


async def _store_id(requested: str) -> str:
    import db

    if requested:
        return requested
    env = os.environ.get("AI_BLOG_BACKEND_STORE_ID", "").strip()
    if env:
        return env
    stores = await db.get_stores()
    if not stores:
        raise SystemExit("No stores configured.")
    return str(stores[0]["id"])


async def cmd_doctor(args) -> None:
    from .health import run_doctor

    report = await run_doctor(await _store_id(args.store), live=not args.offline)
    icons = {"ok": "✓", "warning": "!", "error": "✗", "skipped": "-"}
    print(f"Social Engine doctor — store {report['store_id']} — overall: {report['status'].upper()}")
    print(f"engine enabled: {report['engine_enabled']}  publisher: {report['publisher']}\n")
    for check in report["checks"]:
        print(f" {icons.get(check['status'], '?')} {check['name']:<16} {check['message']}")


async def cmd_worker(args) -> None:
    from .jobs.worker import Worker

    worker = Worker(run_periodic=not args.no_periodic)
    if args.once:
        print(f"Ran {await worker.run_once()} job(s).")
    else:
        await worker.run_forever()


async def cmd_jobs(args) -> None:
    from db.social_engine import jobs as job_store

    rows = await job_store.list_jobs(status=args.status, job_type=args.type, limit=args.limit,
                                     correlation_id=args.correlation)
    for job in rows:
        print(f"{job['id']}  {job['type']:<20} {job['status']:<10} attempts {job['attempts']}/{job['max_attempts']}  "
              f"run_at {_fmt_time(job['run_at'])}  {job['last_error_type']}: {job['last_error'][:120]}")
    print(f"\n{len(rows)} job(s). Counts: {await job_store.counts_by_status()}")


async def cmd_job_retry(args) -> None:
    from db.social_engine import jobs as job_store

    print("requeued" if await job_store.retry(args.job_id) else "not retryable (only failed/cancelled jobs)")


async def cmd_job_cancel(args) -> None:
    from db.social_engine import jobs as job_store

    print("cancelled" if await job_store.cancel(args.job_id) else "only queued jobs can be cancelled")


async def cmd_post(args) -> None:
    from db.social_engine import jobs as job_store, observability, posts as post_store

    store_id = await _store_id(args.store)
    post = await post_store.get_post(store_id, args.post_id)
    if not post:
        raise SystemExit(f"Post {args.post_id} not found in store {store_id}.")
    print(f"POST {post['id']}  [{post['status']}]  {post['content_type']}  template={post['template_key']}")
    print(f"  title: {post['title']}\n  scheduled: {_fmt_time(post['scheduled_at'])}  approval: {post['approval_mode']}")
    if post.get("error_message"):
        print(f"  ERROR {post['error_type']}: {post['error_message']}")
    print("\nTARGETS")
    for t in post["targets"]:
        print(f"  {t['provider']:<12} {t['status']:<10} at {_fmt_time(t['scheduled_at'])}  ext={t['external_id'] or '—'} "
              f"{t['release_url']}\n     {t['caption'][:140]!r}" + (f"\n     ERROR: {t['error_message']}" if t['error_message'] else ""))
    print("\nMEDIA")
    for m in post["media"]:
        print(f"  {m['format']:<9} {m['kind']:<5} {m['role']:<9} {m['width']}x{m['height']} {m['path']}"
              f"  postiz={m['external_id'] or '—'}")
    print("\nTIMELINE (newest first)")
    for row in await observability.list_activity(entity_type="post", entity_id=post["id"], limit=50):
        print(f"  {_fmt_time(row['created_at'])} [{row['level']}] {row['stage']}: {row['message']}")
    print("\nJOBS")
    for job in await job_store.list_jobs(correlation_id=post["id"], limit=20):
        print(f"  {job['type']:<15} {job['status']:<10} attempts {job['attempts']}  {job['last_error'][:120]}")
    print("\nAPI CALLS")
    for call in await observability.list_api_calls(correlation_id=post["id"], limit=20):
        print(f"  {_fmt_time(call['created_at'])} {call['service']}.{call['operation']} {call['method']} "
              f"{call['status_code']} {call['duration_ms']}ms {call['error']}")
    print("\nAI DECISIONS")
    for row in await observability.list_decisions(subject_id=post["id"], limit=10):
        print(f"  {_fmt_time(row['created_at'])} {row['decision_type']} model={row['model']} valid={row['valid']} {row['error']}")


async def cmd_calls(args) -> None:
    from db.social_engine import observability

    since = int(time.time()) - args.hours * 3600
    for call in await observability.list_api_calls(service=args.service, only_errors=args.errors,
                                                   since=since, limit=args.limit):
        print(f"{_fmt_time(call['created_at'])} {call['service']:<8} {call['operation']:<22} {call['method']:<6} "
              f"{call['status_code'] or '---'} {call['duration_ms']:>6}ms try{call['attempt']} {call['error'][:100]}")
    print()
    for row in await observability.api_call_stats(since):
        print(f"{row['service']}.{row['operation']}: {row['calls']} calls, {row['failed_calls']} failed, avg {row['avg_ms']}ms")


async def cmd_sync(args) -> None:
    store_id = await _store_id(args.store)
    if args.what == "catalog":
        from .catalog import sync_catalog

        _print(await sync_catalog(store_id, full=args.full, correlation_id="cli"))
    else:
        from .pipeline.channels import sync_channels

        _print(await sync_channels(store_id))


async def cmd_evaluate(args) -> None:
    from .campaigns.engine import evaluate_campaign, evaluate_due_campaigns

    store_id = await _store_id(args.store)
    if args.campaign:
        _print(await evaluate_campaign(store_id, args.campaign, dry_run=args.dry_run))
    else:
        _print(await evaluate_due_campaigns(store_id))


async def cmd_generate(args) -> None:
    from .pipeline.generate import generate_post

    _print(await generate_post(await _store_id(args.store), args.post_id, force=args.force))


async def cmd_publish(args) -> None:
    from .pipeline.publish import publish_post

    _print(await publish_post(await _store_id(args.store), args.post_id))


async def cmd_reconcile(args) -> None:
    from .pipeline.reconcile import reconcile_store

    _print(await reconcile_store(await _store_id(args.store)))


async def cmd_preview(args) -> None:
    from .creatives import render_preview

    media = await render_preview(await _store_id(args.store), args.template, args.format)
    print(media.path)


async def cmd_seed(args) -> None:
    from .creatives import seed_builtin_templates

    print(f"{await seed_builtin_templates(await _store_id(args.store))} template(s) seeded")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="social_engine", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="", help="store id (default: first store)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check every dependency")
    p.add_argument("--offline", action="store_true", help="skip live calls to Postiz/Gemma/Shopify")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("worker", help="run the job worker")
    p.add_argument("--once", action="store_true", help="run due jobs then exit")
    p.add_argument("--no-periodic", action="store_true", help="don't enqueue recurring jobs")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("jobs", help="list jobs")
    p.add_argument("--status", default="")
    p.add_argument("--type", default="")
    p.add_argument("--correlation", default="")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("job-retry", help="requeue a failed/cancelled job")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_job_retry)

    p = sub.add_parser("job-cancel", help="cancel a queued job")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_job_cancel)

    p = sub.add_parser("post", help="everything about one post")
    p.add_argument("post_id")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("calls", help="external API call log")
    p.add_argument("--service", default="")
    p.add_argument("--errors", action="store_true")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_calls)

    p = sub.add_parser("sync", help="sync catalog or channels now")
    p.add_argument("what", choices=["catalog", "channels"])
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("evaluate", help="evaluate campaigns")
    p.add_argument("--campaign", default="")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("generate", help="generate one post now")
    p.add_argument("post_id")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("publish", help="publish one approved post now")
    p.add_argument("post_id")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("reconcile", help="sync post status from Postiz")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("preview", help="render a template preview")
    p.add_argument("template")
    p.add_argument("format", nargs="?", default="square")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("seed-templates", help="install built-in templates for the store")
    p.set_defaults(func=cmd_seed)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    async def runner() -> None:
        import db

        db.set_db_path(os.environ.get("DB_PATH", str(_SERVER_DIR / "data" / "ai_blog_server.db")))
        await db.init_db()
        await args.func(args)

    asyncio.run(runner())


if __name__ == "__main__":
    sys.exit(main())
