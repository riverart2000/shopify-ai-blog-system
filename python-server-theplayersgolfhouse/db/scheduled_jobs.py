"""db/scheduled_jobs.py — Scheduled blog generation jobs."""
from __future__ import annotations

import time
import uuid
from typing import Optional

import aiosqlite

from .base import get_db_path


async def get_scheduled_jobs(store_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_jobs WHERE store_id=? ORDER BY created_at",
            (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_all_active_jobs() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT sj.*, s.name as store_name "
            "FROM scheduled_jobs sj JOIN stores s ON sj.store_id=s.id "
            "WHERE sj.is_active=1 ORDER BY sj.next_run_at"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_due_jobs() -> list[dict]:
    """Return active jobs whose next_run_at <= now."""
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scheduled_jobs "
            "WHERE is_active=1 AND next_run_at IS NOT NULL AND next_run_at<=?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def upsert_job(job: dict) -> str:
    if not job.get("id"):
        job["id"] = str(uuid.uuid4())
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO scheduled_jobs
                 (id, store_id, name, prompt_id, blog_handle, author,
                  cron_expr, timezone, is_active, next_run_at, is_product_blog, use_keyword_pool)
               VALUES
                 (:id, :store_id, :name, :prompt_id, :blog_handle, :author,
                  :cron_expr, :timezone, :is_active, :next_run_at, :is_product_blog, :use_keyword_pool)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 prompt_id=excluded.prompt_id,
                 blog_handle=excluded.blog_handle,
                 author=excluded.author,
                 cron_expr=excluded.cron_expr,
                 timezone=excluded.timezone,
                 is_active=excluded.is_active,
                 next_run_at=excluded.next_run_at,
                 is_product_blog=excluded.is_product_blog,
                 use_keyword_pool=excluded.use_keyword_pool""",
            {**job, "is_product_blog": job.get("is_product_blog", 0),
             "use_keyword_pool": job.get("use_keyword_pool", 0)},
        )
        await db.commit()
    return job["id"]


async def delete_job(job_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
        await db.commit()


async def update_job_run_times(
    job_id: str, last_run_at: int, next_run_at: Optional[int]
) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE scheduled_jobs SET last_run_at=?, next_run_at=? WHERE id=?",
            (last_run_at, next_run_at, job_id),
        )
        await db.commit()
