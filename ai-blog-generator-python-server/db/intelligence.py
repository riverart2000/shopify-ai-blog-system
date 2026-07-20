"""Persistence for customer-intelligence audits and recommendations."""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

import aiosqlite

from .base import get_db_path


async def create_intelligence_run(
    store_id: str,
    period_days: int,
    trigger_type: str = "manual",
) -> str:
    run_id = str(uuid.uuid4())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO intelligence_runs
               (id, store_id, trigger_type, status, period_days, started_at)
               VALUES (?, ?, ?, 'running', ?, ?)""",
            (run_id, store_id, trigger_type, period_days, int(time.time())),
        )
        await conn.commit()
    return run_id


async def complete_intelligence_run(
    run_id: str,
    summary: dict,
    recommendations: list[dict],
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE intelligence_runs
               SET status='complete', summary_json=?, completed_at=?, error_message=''
               WHERE id=?""",
            (json.dumps(summary, ensure_ascii=False), now, run_id),
        )
        store_id = summary.get("store_id", "")
        for item in recommendations:
            await conn.execute(
                """INSERT INTO intelligence_recommendations
                   (id, run_id, store_id, category, severity, title, evidence,
                    action, confidence, impact, effort, metric_key, source, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    str(uuid.uuid4()), run_id, store_id,
                    item.get("category", "conversion"), item.get("severity", "medium"),
                    item.get("title", "Recommendation"), item.get("evidence", ""),
                    item.get("action", ""), item.get("confidence", "medium"),
                    item.get("impact", "medium"), item.get("effort", "medium"),
                    item.get("metric_key", ""), item.get("source", "rules"), now,
                ),
            )
        await conn.commit()


async def fail_intelligence_run(run_id: str, message: str) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE intelligence_runs
               SET status='failed', error_message=?, completed_at=? WHERE id=?""",
            (message[:1000], int(time.time()), run_id),
        )
        await conn.commit()


def _decode_run(row: aiosqlite.Row | None) -> Optional[dict]:
    if not row:
        return None
    result = dict(row)
    try:
        result["summary"] = json.loads(result.pop("summary_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["summary"] = {}
    return result


async def get_latest_intelligence_run(store_id: str) -> Optional[dict]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE store_id=? ORDER BY started_at DESC LIMIT 1""",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
    return _decode_run(row)


async def get_intelligence_runs(store_id: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE store_id=? ORDER BY started_at DESC LIMIT ?""",
            (store_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_decode_run(row) or {} for row in rows]


async def get_run_recommendations(run_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM intelligence_recommendations
               WHERE run_id=? AND status='open'
               ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        created_at""",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def dismiss_recommendation(store_id: str, recommendation_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE intelligence_recommendations SET status='dismissed'
               WHERE id=? AND store_id=?""",
            (recommendation_id, store_id),
        )
        await conn.commit()


async def get_stores_due_for_intelligence(now: int, interval_hours: int = 24) -> list[dict]:
    cutoff = now - max(1, interval_hours) * 3600
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT s.* FROM stores s
               JOIN store_settings enabled
                 ON enabled.store_id=s.id
                AND enabled.key='intelligence_auto_enabled'
                AND enabled.value='1'
               LEFT JOIN (
                   SELECT store_id, MAX(started_at) AS last_started
                   FROM intelligence_runs WHERE status IN ('running','complete')
                   GROUP BY store_id
               ) latest ON latest.store_id=s.id
               WHERE latest.last_started IS NULL OR latest.last_started < ?""",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]
