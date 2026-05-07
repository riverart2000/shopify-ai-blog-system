"""db/prompts.py — Prompt templates (scoped to a store)."""
from __future__ import annotations

import aiosqlite

from .base import get_db_path


async def get_prompts(store_id: str) -> list[dict]:
    """Return prompts belonging to this store (store_id='') catches legacy global prompts too."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM prompts WHERE store_id=? ORDER BY sort_order, name",
            (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def upsert_prompt(prompt: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO prompts (id, store_id, name, text, sort_order)
               VALUES (:id, :store_id, :name, :text, :sort_order)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 text=excluded.text,
                 sort_order=excluded.sort_order""",
            prompt,
        )
        await db.commit()


async def delete_prompt(prompt_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
        await db.commit()
