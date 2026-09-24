"""Persistent landing-page generation jobs with exact, terminal failures."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import requests

from db.base import get_db_path
from services.landing_pages.product_prompts.pipeline import Pipeline
from services.landing_pages.product_prompts.utils import slugify

log = logging.getLogger("ai_blog_server")
ACTIVE = ("queued", "running")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(get_db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("credit_json", "timeline_json"):
        try:
            data[key.removesuffix("_json")] = json.loads(data.pop(key) or "{}")
        except json.JSONDecodeError:
            data[key.removesuffix("_json")] = {} if key == "credit_json" else []
    return data


def create_job(
    *, shop: str, store_id: str, product_url: str, credit: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Create one job, or return the already-active job for this product."""
    with _connect() as connection:
        row = connection.execute(
            """SELECT * FROM landing_page_jobs
               WHERE shop=? AND product_url=? AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 1""",
            (shop, product_url),
        ).fetchone()
        if row:
            return _decode(row) or {}, False
        job_id = uuid.uuid4().hex
        now = int(time.time())
        timeline = [{
            "at": now, "stage": "queued", "progress": 0,
            "message": "Job accepted. Waiting for the background worker.",
        }]
        connection.execute(
            """INSERT INTO landing_page_jobs
               (id, store_id, shop, product_url, status, stage, progress, message,
                credit_json, timeline_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'queued', 'queued', 0, ?, ?, ?, ?, ?)""",
            (
                job_id, store_id, shop, product_url, timeline[0]["message"],
                json.dumps(credit), json.dumps(timeline), now, now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM landing_page_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return _decode(row) or {}, True


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM landing_page_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return _decode(row)


def fail_interrupted_jobs() -> int:
    """Mark jobs left active by a server stop as terminal instead of hanging."""
    now = int(time.time())
    message = (
        "The server stopped or restarted while this job was active. Its final "
        "outcome cannot be verified, so it was marked failed. It was not retried."
    )
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, timeline_json FROM landing_page_jobs
               WHERE status IN ('queued','running')"""
        ).fetchall()
        for row in rows:
            timeline = json.loads(row["timeline_json"] or "[]")
            timeline.append({
                "at": now,
                "stage": "interrupted",
                "progress": 100,
                "message": message,
            })
            connection.execute(
                """UPDATE landing_page_jobs SET status='failed',
                   stage='interrupted', progress=100, message=?,
                   error_type='job_interrupted', error_message=?,
                   error_detail='', timeline_json=?, updated_at=?,
                   completed_at=? WHERE id=?""",
                (message, message, json.dumps(timeline[-40:]), now, now, row["id"]),
            )
    return len(rows)


def update_progress(job_id: str, stage: str, progress: int, message: str) -> None:
    now = int(time.time())
    with _connect() as connection:
        row = connection.execute(
            "SELECT timeline_json FROM landing_page_jobs WHERE id=?", (job_id,)
        ).fetchone()
        timeline = json.loads(row[0] or "[]") if row else []
        timeline.append({
            "at": now, "stage": stage, "progress": max(0, min(100, progress)),
            "message": message,
        })
        connection.execute(
            """UPDATE landing_page_jobs SET status='running', stage=?, progress=?,
               message=?, timeline_json=?, started_at=COALESCE(started_at, ?),
               updated_at=? WHERE id=?""",
            (
                stage, max(0, min(100, progress)), message,
                json.dumps(timeline[-40:]), now, now, job_id,
            ),
        )


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "provider_timeout"
    if isinstance(exc, requests.HTTPError):
        return "provider_http_error"
    if isinstance(exc, requests.RequestException):
        return "network_error"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_provider_response"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "validation_error"
    return type(exc).__name__


def fail_job(job_id: str, exc: Exception) -> None:
    now = int(time.time())
    error_type = getattr(exc, "error_type", "") or _classify_error(exc)
    error_message = str(exc).strip() or repr(exc)
    detail = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )[-12000:]
    with _connect() as connection:
        row = connection.execute(
            "SELECT stage, timeline_json FROM landing_page_jobs WHERE id=?", (job_id,)
        ).fetchone()
        stage = row["stage"] if row else "unknown"
        timeline = json.loads(row["timeline_json"] or "[]") if row else []
        message = (
            f"Failed during '{stage}': {error_message}. "
            "The job is final; no retry or fallback was attempted."
        )
        timeline.append({
            "at": now, "stage": stage, "progress": 100, "message": message,
        })
        connection.execute(
            """UPDATE landing_page_jobs SET status='failed', progress=100,
               message=?, error_type=?, error_message=?, error_detail=?,
               timeline_json=?, updated_at=?, completed_at=? WHERE id=?""",
            (
                message, error_type, error_message, detail,
                json.dumps(timeline[-40:]), now, now, job_id,
            ),
        )
    log.exception("Landing-page job %s failed with no retry", job_id)


def complete_job(job_id: str, out_path: Path) -> None:
    now = int(time.time())
    message = "Generation completed successfully with one Grok request."
    with _connect() as connection:
        row = connection.execute(
            "SELECT timeline_json FROM landing_page_jobs WHERE id=?", (job_id,)
        ).fetchone()
        timeline = json.loads(row[0] or "[]") if row else []
        timeline.append({
            "at": now, "stage": "completed", "progress": 100, "message": message,
        })
        connection.execute(
            """UPDATE landing_page_jobs SET status='succeeded', stage='completed',
               progress=100, message=?, product_handle=?, result_path=?,
               timeline_json=?, updated_at=?, completed_at=? WHERE id=?""",
            (
                message, out_path.stem, str(out_path), json.dumps(timeline[-40:]),
                now, now, job_id,
            ),
        )


def run_job(job_id: str, settings: Any, fetcher: str, generator: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    try:
        update_progress(job_id, "starting", 5, "Background worker started.")
        pipeline = Pipeline(
            settings,
            fetcher_name=fetcher,
            generator_name=generator,
            progress_callback=lambda stage, progress, message: update_progress(
                job_id, stage, progress, message
            ),
        )
        out_path = pipeline.process_one(job["product_url"])
        complete_job(job_id, out_path)
    except Exception as exc:  # exact terminal failure is persisted
        fail_job(job_id, exc)
