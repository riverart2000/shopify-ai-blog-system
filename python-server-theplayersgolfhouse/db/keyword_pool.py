"""db/keyword_pool.py — Long-tail keyword pool CRUD."""
from __future__ import annotations

from typing import Optional

import aiosqlite

from .base import get_db_path


async def add_keywords(
    store_id: str,
    keywords: list[dict],
    max_pool: int = 100,
    source: str = "",
) -> int:
    """Insert new keywords (deduplicated by UNIQUE constraint), respecting max_pool cap.

    ``keywords`` is a list of dicts with keys ``keyword`` (required) and ``content`` (optional).
    Returns the number of rows actually inserted.
    """
    if not keywords:
        return 0
    inserted = 0
    async with aiosqlite.connect(get_db_path()) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM keyword_pool WHERE store_id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
        current_count = row[0] if row else 0

        for item in keywords:
            kw = item.get("keyword", "").strip() if isinstance(item, dict) else str(item).strip()
            content = item.get("content", "").strip() if isinstance(item, dict) else ""
            if not kw:
                continue
            if current_count >= max_pool:
                break
            try:
                result = await conn.execute(
                    "INSERT OR IGNORE INTO keyword_pool (store_id, keyword, content, source) VALUES (?, ?, ?, ?)",
                    (store_id, kw, content, source),
                )
                if result.rowcount:
                    inserted += 1
                    current_count += 1
            except Exception:
                pass
        await conn.commit()
    return inserted


async def get_keyword_pool(store_id: str, limit: int = 200) -> list[dict]:
    """Return up to `limit` keywords ordered oldest-first."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, keyword, content, source, created_at FROM keyword_pool "
            "WHERE store_id=? ORDER BY created_at ASC, id ASC LIMIT ?",
            (store_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_keyword_pool(store_id: str) -> int:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM keyword_pool WHERE store_id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def peek_keyword(store_id: str) -> Optional[dict]:
    """Return the oldest keyword without deleting it."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, keyword, content, source FROM keyword_pool "
            "WHERE store_id=? ORDER BY created_at ASC, id ASC LIMIT 1",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def pop_keyword(store_id: str) -> Optional[dict]:
    """Return AND delete the oldest keyword. Returns {id, keyword} or None."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, keyword, content, source FROM keyword_pool "
            "WHERE store_id=? ORDER BY created_at ASC, id ASC LIMIT 1",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        result = dict(row)
        await db.execute("DELETE FROM keyword_pool WHERE id=?", (result["id"],))
        await db.commit()
    return result


async def delete_keyword(keyword_id: int) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM keyword_pool WHERE id=?", (keyword_id,))
        await db.commit()


async def clear_keyword_pool(store_id: str) -> int:
    """Delete all keywords for a store. Returns count deleted."""
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM keyword_pool WHERE store_id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
        count = row[0] if row else 0
        await db.execute("DELETE FROM keyword_pool WHERE store_id=?", (store_id,))
        await db.commit()
    return count
