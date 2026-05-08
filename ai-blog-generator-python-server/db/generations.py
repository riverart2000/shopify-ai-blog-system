"""db/generations.py — Generation history and per-model error log."""
from __future__ import annotations

import json
from typing import Optional

import aiosqlite

from .base import get_db_path


async def log_generation(
    store_id: str,
    store_name: str,
    blog_handle: str,
    prompt_id: str,
    prompt_text: str,
    title: str,
    summary: str,
    *,
    content_text: str = "",
    keywords: list[str],
    hashtags: list[str],
    image_count: int,
    article_id: Optional[str] = None,
    article_url: Optional[str] = None,
    status: str = "published",
    scheduled_job_id: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO generations
                 (store_id, store_name, blog_handle, prompt_id, prompt_text,
                  title, summary, content_text, keywords, hashtags, image_count,
                  article_id, article_url, status, scheduled_job_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                store_id, store_name, blog_handle, prompt_id, prompt_text,
                title, summary, content_text,
                json.dumps(keywords), json.dumps(hashtags),
                image_count, article_id, article_url, status, scheduled_job_id,
            ),
        )
        await db.commit()


async def get_recent_generations(
    store_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if store_id:
            async with db.execute(
                "SELECT * FROM generations WHERE store_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (store_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM generations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()

    result = []
    for r in rows:
        row = dict(r)
        for field in ("keywords", "hashtags"):
            try:
                row[field] = json.loads(row.get(field, "[]"))
            except Exception:
                row[field] = []
        result.append(row)
    return result


async def get_recent_runs_for_job(job_id: str, limit: int = 10) -> list[dict]:
    """Return the most recent published generations for a specific scheduled job."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, article_url, blog_handle, created_at "
            "FROM generations WHERE scheduled_job_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (job_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def log_model_error(
    store_id: str,
    model_id: Optional[str],
    provider: Optional[str],
    error_type: str,
    message: str,
) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO generation_errors "
            "(store_id,model_id,provider,error_type,message) VALUES (?,?,?,?,?)",
            (store_id, model_id, provider, error_type, message),
        )
        await db.commit()


async def get_recent_errors(store_id: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM generation_errors WHERE store_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (store_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
