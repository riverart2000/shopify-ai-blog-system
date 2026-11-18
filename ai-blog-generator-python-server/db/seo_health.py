"""What the daily search-health check found: each blog post's standing with Google, and one summary per run."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import aiosqlite

from .base import get_db_path


async def set_index_status(store_id: str, path: str, *, coverage_state: str, verdict: str, last_crawl: str) -> None:
    """Record Google's verdict on a page; `state_since` only moves when the state changes."""
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO seo_index_status (store_id, path, coverage_state, verdict, last_crawl, state_since, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(store_id, path) DO UPDATE SET
                 state_since = CASE WHEN seo_index_status.coverage_state = excluded.coverage_state
                                    THEN seo_index_status.state_since ELSE excluded.state_since END,
                 coverage_state = excluded.coverage_state, verdict = excluded.verdict,
                 last_crawl = excluded.last_crawl, checked_at = excluded.checked_at""",
            (store_id, path, coverage_state, verdict, last_crawl, now, now),
        )
        await conn.commit()


async def list_index_status(store_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM seo_index_status WHERE store_id=?", (store_id,)) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def forget_index_status(store_id: str, paths: list[str]) -> None:
    if not paths:
        return
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.executemany("DELETE FROM seo_index_status WHERE store_id=? AND path=?", [(store_id, p) for p in paths])
        await conn.commit()


async def add_health_run(store_id: str, summary: dict[str, Any], *, keep_days: int) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("INSERT INTO seo_health_runs (store_id, summary_json) VALUES (?, ?)",
                           (store_id, json.dumps(summary, ensure_ascii=False)))
        await conn.execute("DELETE FROM seo_health_runs WHERE store_id=? AND created_at<?",
                           (store_id, int(time.time()) - keep_days * 86400))
        await conn.commit()


async def list_health_runs(store_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT created_at, summary_json FROM seo_health_runs WHERE store_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (store_id, int(limit)),
        ) as cur:
            return [{"created_at": row["created_at"], **json.loads(row["summary_json"] or "{}")} for row in await cur.fetchall()]


async def health_run_before(store_id: str, before: int) -> Optional[dict[str, Any]]:
    """The latest run at or before the time `before` (for comparing with a week ago)."""
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT created_at, summary_json FROM seo_health_runs WHERE store_id=? AND created_at<=? ORDER BY created_at DESC LIMIT 1",
            (store_id, int(before)),
        ) as cur:
            row = await cur.fetchone()
    return {"created_at": row["created_at"], **json.loads(row["summary_json"] or "{}")} if row else None
