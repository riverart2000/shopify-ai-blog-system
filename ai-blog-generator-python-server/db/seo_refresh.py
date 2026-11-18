"""Log of blog posts refreshed for a real search phrase (what was there before, what was written)."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import aiosqlite

from .base import get_db_path


def _row(row: aiosqlite.Row) -> dict[str, Any]:
    item = dict(row)
    item["before"] = json.loads(item.pop("before_json") or "{}")
    item["after"] = json.loads(item.pop("after_json") or "{}")
    return item


async def record_seo_refresh(
    store_id: str, *, article_gid: str, path: str, phrase: str, impressions: int, position: float,
    before: dict[str, Any], after: dict[str, Any], status: str = "applied", note: str = "",
) -> str:
    refresh_id = uuid.uuid4().hex
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO seo_refreshes
               (id, store_id, article_gid, path, phrase, impressions, position, before_json, after_json, status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (refresh_id, store_id, article_gid, path, phrase, int(impressions), float(position),
             json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), status, note),
        )
        await conn.commit()
    return refresh_id


async def list_seo_refreshes(store_id: str, *, since: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_refreshes WHERE store_id=? AND created_at>=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (store_id, int(since), int(limit)),
        ) as cur:
            return [_row(row) for row in await cur.fetchall()]


async def get_seo_refresh(store_id: str, refresh_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM seo_refreshes WHERE store_id=? AND id=?", (store_id, refresh_id),
        ) as cur:
            row = await cur.fetchone()
    return _row(row) if row else None


async def mark_seo_refresh_restored(refresh_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            "UPDATE seo_refreshes SET status='restored', restored_at=? WHERE id=?", (int(time.time()), refresh_id),
        )
        await conn.commit()
