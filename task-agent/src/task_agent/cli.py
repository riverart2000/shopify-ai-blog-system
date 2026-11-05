"""Command line: `uv run task-agent <command>`.

enqueue <type> '<json payload>'   add a job (e.g. research '{"question": "..."}')
research "<question>"             shortcut for a research job
run-once                          run ready jobs now, then exit
worker                            run forever, one job at a time
jobs [--status S]                 list jobs
show <id>                         one job with its steps and result
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

import structlog
from pydantic import ValidationError

from task_agent.config import get_settings
from task_agent.db import JobStore
from task_agent.promotion import MAX_IMAGE_BYTES, set_picture
from task_agent.tasks import TASKS
from task_agent.worker import Worker


def setup_logging(json_logs: bool) -> None:
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def store() -> JobStore:
    settings = get_settings()
    return JobStore(settings.path(settings.db_path))


def enqueue(
    job_type: str,
    payload: dict,
    *,
    title: str | None,
    key: str | None,
    priority: int,
    run_at: str | None,
    created_by: str = "cli",
) -> int:
    spec = TASKS.get(job_type)
    if spec is None:
        sys.exit(f"Unknown task type '{job_type}'. Known: {', '.join(TASKS)}")
    try:
        validated = spec.payload_model.model_validate(payload)
    except ValidationError as exc:
        sys.exit(f"Bad payload for {job_type}:\n{exc}")
    when = datetime.fromisoformat(run_at) if run_at else None
    first_value = next(iter(validated.model_dump().values()), job_type)
    job_id, created = store().enqueue(
        job_type,
        title or str(first_value)[:120],
        validated.model_dump(mode="json"),
        spec.tool_names,
        priority=priority,
        run_at=when,
        idempotency_key=key,
        created_by=created_by,
    )
    print(f"{'Queued' if created else 'Already queued (same key)'}: job {job_id}")
    return job_id


def cmd_jobs(status: str | None) -> None:
    for job in store().list(status):
        print(f"{job.id:>5}  {job.status:<16} {job.type:<14} tries={job.attempts}  {job.title[:70]}")


def cmd_show(job_id: int) -> None:
    s = store()
    job = s.get(job_id)
    if job is None:
        sys.exit(f"No job {job_id}")
    print(f"Job {job.id} [{job.status}] {job.type}: {job.title}")
    print(f"  created {job.created_at}  started {job.started_at}  finished {job.finished_at}  tries {job.attempts}")
    if job.error:
        print(f"  error: {job.error}")
    for step in s.steps(job_id):
        secs = (step["duration_ms"] or 0) / 1000
        print(f"  {step['n']:>3}. {step['tool']:<14} {step['args'][:80]}  ({secs:.1f}s)")
        print(f"       → {step['result_summary'][:160]}")
    if job.result:
        print("\nResult:\n" + json.dumps(job.result, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="task-agent", description="Background task agents (one job at a time)")
    parser.add_argument("--json-logs", action="store_true", help="JSON log lines (for launchd/n8n)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enqueue", help="Add a job")
    p.add_argument("type", choices=sorted(TASKS))
    p.add_argument("payload", help="JSON object")
    for q in (p, r := sub.add_parser("research", help="Add a research job")):
        q.add_argument("--title")
        q.add_argument("--key", help="Idempotency key: the same key never creates a second job")
        q.add_argument("--priority", type=int, default=0)
        q.add_argument("--run-at", help="ISO time, e.g. 2026-11-01T09:00:00+00:00")
    r.add_argument("question")
    r.add_argument("--context", default="")

    sub.add_parser("run-once", help="Run ready jobs now, then exit")
    sub.add_parser("worker", help="Run forever")
    j = sub.add_parser("jobs", help="List jobs")
    j.add_argument("--status")
    s = sub.add_parser("show", help="Show one job")
    s.add_argument("job_id", type=int)
    pic = sub.add_parser("picture", help="Give a promotion its event picture (its unfinished posts get it)")
    pic.add_argument("promotion_id", type=int)
    pic.add_argument("source", help="The picture's web address, or a picture file on this Mac")

    args = parser.parse_args(argv)
    setup_logging(args.json_logs)
    settings = get_settings()

    if args.cmd == "enqueue":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            sys.exit(f"Payload isn't valid JSON: {exc}")
        enqueue(args.type, payload, title=args.title, key=args.key, priority=args.priority, run_at=args.run_at)
    elif args.cmd == "research":
        enqueue(
            "research",
            {"question": args.question, "context": args.context},
            title=args.title,
            key=args.key,
            priority=args.priority,
            run_at=args.run_at,
        )
    elif args.cmd == "run-once":
        ran = asyncio.run(Worker(store(), settings).drain())
        print(f"Ran {ran} job(s).")
    elif args.cmd == "worker":
        asyncio.run(Worker(store(), settings).run_forever())
    elif args.cmd == "jobs":
        cmd_jobs(args.status)
    elif args.cmd == "show":
        cmd_show(args.job_id)
    elif args.cmd == "picture":
        folder = settings.path(settings.db_path).parent / "promotions" / str(args.promotion_id)
        path, posts = asyncio.run(set_picture(store(), folder, args.promotion_id, args.source))
        if not path:
            sys.exit(
                f"That isn't a picture this Mac could fetch or read (an image up to {MAX_IMAGE_BYTES // 10**6} MB)."
            )
        print(f"Saved {path}; {posts} post(s) of promotion {args.promotion_id} will use it.")


if __name__ == "__main__":
    main()
