"""Persistence for deterministic, reversible SEO repair jobs."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import aiosqlite

from .base import get_db_path


def _row(row: aiosqlite.Row | None) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


async def create_seo_repair_job(store_id: str, rule_key: str) -> str:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("BEGIN IMMEDIATE")
        async with conn.execute(
            """SELECT id FROM seo_repair_jobs
               WHERE store_id=? AND status IN ('queued','running','applying','restoring')
               ORDER BY created_at DESC LIMIT 1""",
            (store_id,),
        ) as cur:
            active = await cur.fetchone()
        if active:
            await conn.commit()
            return ""
        job_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO seo_repair_jobs
               (id,store_id,rule_key,status,stage,progress,created_at,updated_at)
               VALUES (?,?,?,'queued','queued',0,?,?)""",
            (job_id, store_id, rule_key, now, now),
        )
        await conn.commit()
        return job_id


async def get_latest_seo_repair_job(store_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_repair_jobs WHERE store_id=? ORDER BY created_at DESC LIMIT 1",
            (store_id,),
        ) as cur:
            return _row(await cur.fetchone())


async def get_seo_repair_job(store_id: str, job_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_repair_jobs WHERE id=? AND store_id=?",
            (job_id, store_id),
        ) as cur:
            return _row(await cur.fetchone())


async def update_seo_repair_job(
    job_id: str,
    *,
    status: str,
    stage: str,
    progress: int,
    total_items: int | None = None,
    processed_items: int | None = None,
    changed_items: int | None = None,
    skipped_items: int | None = None,
    failed_items: int | None = None,
    message: str | None = None,
    complete: bool = False,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "progress": min(max(int(progress), 0), 100),
        "updated_at": int(time.time()),
    }
    for key, value in {
        "total_items": total_items,
        "processed_items": processed_items,
        "changed_items": changed_items,
        "skipped_items": skipped_items,
        "failed_items": failed_items,
        "message": message,
    }.items():
        if value is not None:
            values[key] = value
    if status in {"running", "applying", "restoring"}:
        values["started_at"] = int(time.time())
    if complete:
        values["completed_at"] = int(time.time())
    assignments = ",".join(f"{key}=?" for key in values)
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            f"UPDATE seo_repair_jobs SET {assignments} WHERE id=?",  # noqa: S608
            [*values.values(), job_id],
        )
        await conn.commit()


async def begin_seo_repair_phase(
    store_id: str,
    job_id: str,
    *,
    expected_status: str,
    status: str,
    stage: str,
) -> bool:
    """Atomically claim an apply/restore phase so duplicate clicks are harmless."""
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            """UPDATE seo_repair_jobs
               SET status=?,stage=?,progress=0,processed_items=0,changed_items=0,
                   skipped_items=0,failed_items=0,error_type='',error_message='',
                   message='',started_at=?,updated_at=?,completed_at=NULL
               WHERE id=? AND store_id=? AND status=?""",
            (status, stage, now, now, job_id, store_id, expected_status),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def replace_seo_repair_items(
    job_id: str,
    store_id: str,
    rule_key: str,
    items: list[dict[str, Any]],
) -> None:
    now = int(time.time())
    rows = [
        (
            str(uuid.uuid4()), job_id, store_id, rule_key,
            str(item.get("resource_type", "article")), str(item["resource_id"]),
            str(item.get("parent_id", "")), str(item.get("title", "")),
            str(item.get("page_url", "")), str(item["original_html"]),
            str(item["repaired_html"]), str(item["original_hash"]),
            str(item["repaired_hash"]), "ready", "", now, now,
        )
        for item in items
    ]
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("DELETE FROM seo_repair_items WHERE job_id=?", (job_id,))
        if rows:
            await conn.executemany(
                """INSERT INTO seo_repair_items
                   (id,job_id,store_id,rule_key,resource_type,resource_id,parent_id,
                    title,page_url,original_html,repaired_html,original_hash,repaired_hash,
                    status,error_message,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        await conn.commit()


async def list_seo_repair_items(
    store_id: str,
    job_id: str,
    *,
    limit: int = 20,
    include_html: bool = False,
) -> list[dict[str, Any]]:
    columns = "*" if include_html else (
        "id,job_id,store_id,rule_key,resource_type,resource_id,parent_id,title,"
        "page_url,original_hash,repaired_hash,status,error_message,created_at,updated_at,restored_at"
    )
    sql = (
        f"SELECT {columns} FROM seo_repair_items "  # noqa: S608
        "WHERE store_id=? AND job_id=? ORDER BY created_at, title LIMIT ?"
    )
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, (store_id, job_id, min(max(int(limit), 1), 50000))) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def set_seo_repair_item_result(
    item_id: str,
    *,
    status: str,
    error_message: str = "",
    restored: bool = False,
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_repair_items
               SET status=?,error_message=?,updated_at=?,restored_at=? WHERE id=?""",
            (status, error_message[:4000], now, now if restored else None, item_id),
        )
        await conn.commit()


async def set_seo_repair_item_applied(
    item_id: str,
    repaired_html: str,
    repaired_hash: str,
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_repair_items
               SET repaired_html=?,repaired_hash=?,status='changed',error_message='',
                   updated_at=?,restored_at=NULL WHERE id=?""",
            (repaired_html, repaired_hash, now, item_id),
        )
        await conn.commit()


async def fail_seo_repair_job(job_id: str, error_type: str, message: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """UPDATE seo_repair_jobs
               SET status='failed',stage='failed',error_type=?,error_message=?,
                   message='Repair stopped. No retry was attempted.',updated_at=?,completed_at=?
               WHERE id=?""",
            (error_type[:120], message[:4000], now, now, job_id),
        )
        await conn.commit()


async def fail_interrupted_seo_repair_jobs() -> int:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            """UPDATE seo_repair_jobs
               SET status='failed',stage='failed',progress=0,
                   error_type='ServiceRestart',
                   error_message='The SEO repair was interrupted by a service restart. No retry was attempted.',
                   message='Repair stopped. No retry was attempted.',updated_at=?,completed_at=?
               WHERE status IN ('queued','running','applying','restoring')""",
            (now, now),
        )
        await conn.commit()
        return cursor.rowcount
