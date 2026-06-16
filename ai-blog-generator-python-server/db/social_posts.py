"""db/social_posts.py — Social post history persisted from Publer workflows."""
from __future__ import annotations

import json
from typing import Optional

import aiosqlite

from .base import get_db_path


async def log_social_post(
    *,
    store_id: str,
    store_name: str,
    workspace_id: str,
    campaign_name: str,
    product_handle: str,
    product_title: str,
    brief_text: str,
    base_text: str,
    provider_texts: dict[str, str],
    account_ids: list[str],
    mode: str,
    scheduled_at: Optional[str],
    publer_job_id: str,
    publer_status: str,
    publer_failures: list[dict] | list[str] | None = None,
) -> int:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute(
            """INSERT INTO social_posts
                 (store_id, store_name, workspace_id, campaign_name, product_handle,
                  product_title, brief_text, base_text, provider_texts_json,
                  account_ids_json, mode, scheduled_at, publer_job_id,
                  publer_status, publer_failures)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                store_id,
                store_name,
                workspace_id,
                campaign_name,
                product_handle,
                product_title,
                brief_text,
                base_text,
                json.dumps(provider_texts or {}),
                json.dumps(account_ids or []),
                mode,
                scheduled_at,
                publer_job_id,
                publer_status,
                json.dumps(publer_failures or []),
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_recent_social_posts(store_id: str, limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM social_posts WHERE store_id=? ORDER BY created_at DESC LIMIT ?",
            (store_id, max(1, min(limit, 200))),
        ) as cur:
            rows = await cur.fetchall()

    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["provider_texts"] = json.loads(item.get("provider_texts_json") or "{}")
        except Exception:
            item["provider_texts"] = {}
        try:
            item["account_ids"] = json.loads(item.get("account_ids_json") or "[]")
        except Exception:
            item["account_ids"] = []
        try:
            item["publer_failures_list"] = json.loads(item.get("publer_failures") or "[]")
        except Exception:
            item["publer_failures_list"] = []
        result.append(item)
    return result


async def update_social_post_job_status(
    *,
    publer_job_id: str,
    status: str,
    failures: list[dict] | list[str] | None = None,
) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE social_posts SET publer_status=?, publer_failures=? WHERE publer_job_id=?",
            (
                status,
                json.dumps(failures or []),
                publer_job_id,
            ),
        )
        await db.commit()
