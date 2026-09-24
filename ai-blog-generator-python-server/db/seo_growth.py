"""Persistence boundary for SEO Growth runs, opportunities and backlinks."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import aiosqlite

from .base import get_db_path


def _decode_run(row: aiosqlite.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["summary"] = json.loads(item.pop("summary_json", "{}") or "{}")
    except json.JSONDecodeError:
        item["summary"] = {}
    return item


def _decode_opportunity(row: aiosqlite.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["metrics"] = json.loads(item.pop("metrics_json", "{}") or "{}")
    except json.JSONDecodeError:
        item["metrics"] = {}
    return item


async def create_seo_growth_run(store_id: str, period_days: int, trigger_type: str = "manual") -> str:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("BEGIN IMMEDIATE")
        async with conn.execute(
            """SELECT id FROM seo_growth_runs
               WHERE store_id=? AND status IN ('queued','running')
               ORDER BY started_at DESC LIMIT 1""",
            (store_id,),
        ) as cur:
            active = await cur.fetchone()
        if active:
            await conn.commit()
            return ""
        run_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO seo_growth_runs
               (id, store_id, trigger_type, status, stage, progress, period_days, started_at, updated_at)
               VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?, ?)""",
            (run_id, store_id, trigger_type, period_days, now, now),
        )
        await conn.commit()
    return run_id


async def update_seo_growth_run(
    run_id: str,
    *,
    status: str,
    stage: str,
    progress: int,
) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_growth_runs
               SET status=?, stage=?, progress=?, updated_at=? WHERE id=?""",
            (status, stage, min(max(int(progress), 0), 100), int(time.time()), run_id),
        )
        await conn.commit()


async def complete_seo_growth_run(
    run_id: str,
    store_id: str,
    summary: dict[str, Any],
    opportunities: list[dict[str, Any]],
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_growth_runs
               SET status='complete', stage='complete', progress=100, summary_json=?,
                   error_type='', error_message='', updated_at=?, completed_at=?
               WHERE id=?""",
            (json.dumps(summary, ensure_ascii=False), now, now, run_id),
        )
        for item in opportunities:
            async with conn.execute(
                """SELECT status FROM seo_growth_opportunities
                   WHERE store_id=? AND opportunity_key=? AND run_id<>?
                   ORDER BY created_at DESC LIMIT 1""",
                (store_id, item["key"], run_id),
            ) as status_cur:
                previous_status_row = await status_cur.fetchone()
            inherited_status = previous_status_row[0] if previous_status_row else "open"
            await conn.execute(
                """INSERT OR IGNORE INTO seo_growth_opportunities
                   (id, run_id, store_id, opportunity_key, kind, category, severity,
                    score, title, evidence, action, page_url, search_query,
                    metrics_json, source, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), run_id, store_id, item["key"],
                    item.get("kind", "content"), item.get("category", "content"),
                    item.get("severity", "medium"), int(item.get("score", 0)),
                    item.get("title", "SEO opportunity"), item.get("evidence", ""),
                    item.get("action", ""), item.get("page_url", ""),
                    item.get("search_query", ""),
                    json.dumps(item.get("metrics", {}), ensure_ascii=False),
                    item.get("source", "shopify"), inherited_status, now, now,
                ),
            )
        await conn.commit()


async def fail_seo_growth_run(run_id: str, error_type: str, message: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_growth_runs
               SET status='failed', stage='failed', error_type=?, error_message=?,
                   updated_at=?, completed_at=? WHERE id=?""",
            (error_type[:120], message[:4000], now, now, run_id),
        )
        await conn.commit()


async def get_latest_seo_growth_run(store_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_growth_runs WHERE store_id=? ORDER BY started_at DESC LIMIT 1",
            (store_id,),
        ) as cur:
            return _decode_run(await cur.fetchone())


async def get_active_seo_growth_run(store_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM seo_growth_runs
               WHERE store_id=? AND status IN ('queued','running')
               ORDER BY started_at DESC LIMIT 1""",
            (store_id,),
        ) as cur:
            return _decode_run(await cur.fetchone())


async def get_seo_growth_runs(store_id: str, limit: int = 10) -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_growth_runs WHERE store_id=? ORDER BY started_at DESC LIMIT ?",
            (store_id, min(max(int(limit), 1), 50)),
        ) as cur:
            return [_decode_run(row) for row in await cur.fetchall() if row is not None]


async def get_seo_growth_opportunities(
    store_id: str,
    run_id: str = "",
    status: str = "open",
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["store_id=?"]
    values: list[Any] = [store_id]
    if run_id:
        clauses.append("run_id=?")
        values.append(run_id)
    if status:
        clauses.append("status=?")
        values.append(status)
    values.append(min(max(int(limit), 1), 500))
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"""SELECT * FROM seo_growth_opportunities
                WHERE {' AND '.join(clauses)}
                ORDER BY score DESC, created_at DESC LIMIT ?""",  # noqa: S608
            values,
        ) as cur:
            return [_decode_opportunity(row) for row in await cur.fetchall()]


async def set_seo_opportunity_status(store_id: str, opportunity_id: str, status: str) -> bool:
    allowed = {"open", "planned", "in_progress", "done", "dismissed"}
    if status not in allowed:
        raise ValueError(f"Unsupported SEO opportunity status: {status}")
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            """UPDATE seo_growth_opportunities SET status=?, updated_at=?
               WHERE id=? AND store_id=?""",
            (status, int(time.time()), opportunity_id, store_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def upsert_backlink_prospect(store_id: str, values: dict[str, Any]) -> str:
    prospect_id = str(values.get("id") or uuid.uuid4())
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO seo_backlink_prospects
               (id, store_id, domain, prospect_url, contact_name, contact_email,
                target_url, outreach_angle, relationship_type, status, link_url,
                link_rel, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 domain=excluded.domain, prospect_url=excluded.prospect_url,
                 contact_name=excluded.contact_name, contact_email=excluded.contact_email,
                 target_url=excluded.target_url, outreach_angle=excluded.outreach_angle,
                 relationship_type=excluded.relationship_type, status=excluded.status,
                 link_url=excluded.link_url, link_rel=excluded.link_rel,
                 notes=excluded.notes, updated_at=excluded.updated_at
               WHERE seo_backlink_prospects.store_id=excluded.store_id""",
            (
                prospect_id, store_id, str(values.get("domain", "")).strip().lower(),
                str(values.get("prospect_url", "")).strip(),
                str(values.get("contact_name", "")).strip(),
                str(values.get("contact_email", "")).strip(),
                str(values.get("target_url", "")).strip(),
                str(values.get("outreach_angle", "")).strip(),
                str(values.get("relationship_type", "earned")).strip(),
                str(values.get("status", "prospect")).strip(),
                str(values.get("link_url", "")).strip(),
                str(values.get("link_rel", "")).strip(),
                str(values.get("notes", "")).strip(), now, now,
            ),
        )
        await conn.commit()
    return prospect_id


async def list_backlink_prospects(store_id: str, limit: int = 200) -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM seo_backlink_prospects WHERE store_id=?
               ORDER BY updated_at DESC LIMIT ?""",
            (store_id, min(max(int(limit), 1), 500)),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def delete_backlink_prospect(store_id: str, prospect_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            "DELETE FROM seo_backlink_prospects WHERE id=? AND store_id=?",
            (prospect_id, store_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_stores_due_for_seo_growth(now: int, interval_hours: int = 24 * 7) -> list[dict[str, Any]]:
    cutoff = now - max(1, interval_hours) * 3600
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT s.* FROM stores s
               JOIN store_settings enabled
                 ON enabled.store_id=s.id
                AND enabled.key='seo_growth_auto_enabled'
                AND enabled.value='1'
               LEFT JOIN (
                   SELECT store_id, MAX(started_at) AS last_started
                   FROM seo_growth_runs WHERE status IN ('queued','running','complete')
                   GROUP BY store_id
               ) latest ON latest.store_id=s.id
               WHERE latest.last_started IS NULL OR latest.last_started < ?""",
            (cutoff,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def fail_interrupted_seo_growth_runs(trigger_type: str) -> int:
    """Mark abandoned process-local jobs final after a service restart; never retry them."""
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            """UPDATE seo_growth_runs
               SET status='failed', stage='failed', progress=0,
                   error_type='ServiceRestart',
                   error_message='The SEO audit was interrupted by a service restart. No retry was attempted.',
                   updated_at=?, completed_at=?
               WHERE status IN ('queued','running') AND trigger_type=?""",
            (now, now, trigger_type),
        )
        await conn.commit()
        return cursor.rowcount
