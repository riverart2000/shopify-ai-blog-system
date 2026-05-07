"""db/title_pool.py — Pre-generated blog title pool."""
from __future__ import annotations

from typing import Optional

import aiosqlite

from .base import get_db_path


async def add_titles(store_id: str, titles: list[dict]) -> int:
    """Insert titles into the pool, skipping duplicates. Returns count inserted."""
    added = 0
    async with aiosqlite.connect(get_db_path()) as db:
        for t in titles:
            title_str = str(t.get("title", "")).strip()
            if not title_str:
                continue
            cur = await db.execute(
                """INSERT OR IGNORE INTO blog_title_pool
                   (store_id, title, keyword, search_intent, meta_description)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    store_id,
                    title_str,
                    str(t.get("keyword", "")).strip(),
                    str(t.get("search_intent", "")).strip(),
                    str(t.get("meta_description", "")).strip()[:160],
                ),
            )
            added += cur.rowcount
        await db.commit()
    return added


async def pop_title(store_id: str) -> Optional[dict]:
    """Return the oldest unused title and mark it used. Returns None if pool is empty."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, keyword, search_intent, meta_description "
            "FROM blog_title_pool WHERE store_id=? AND used=0 ORDER BY id ASC LIMIT 1",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute(
            "UPDATE blog_title_pool SET used=1 WHERE id=?", (row["id"],)
        )
        await db.commit()
    return dict(row)


async def count_title_pool(store_id: str, unused_only: bool = True) -> int:
    """Count titles in the pool."""
    where = "store_id=? AND used=0" if unused_only else "store_id=?"
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            f"SELECT COUNT(*) FROM blog_title_pool WHERE {where}", (store_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def get_title_pool(store_id: str, include_used: bool = False, limit: int = 200) -> list[dict]:
    """Return titles from the pool ordered oldest-first."""
    where = "store_id=?" if include_used else "store_id=? AND used=0"
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT id, title, keyword, search_intent, meta_description, used, created_at "
            f"FROM blog_title_pool WHERE {where} ORDER BY id ASC LIMIT ?",
            (store_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_title(title_id: int) -> None:
    """Delete a single title by ID."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM blog_title_pool WHERE id=?", (title_id,))
        await db.commit()


async def mark_title_published(title_id: int) -> None:
    """Stamp a title pool entry with the current UTC time it was actually published."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE blog_title_pool SET used=1, used_at=strftime('%s','now') WHERE id=?",
            (title_id,),
        )
        await db.commit()


async def clear_title_pool(store_id: str) -> int:
    """Delete all titles (used and unused) for a store. Returns count deleted."""
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute(
            "DELETE FROM blog_title_pool WHERE store_id=?", (store_id,)
        )
        await db.commit()
    return cur.rowcount
