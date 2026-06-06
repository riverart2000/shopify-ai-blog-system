"""db/models.py — AI model configurations per store."""
from __future__ import annotations

import uuid
from typing import Optional

import aiosqlite

from .base import get_db_path


async def get_models(store_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM models WHERE store_id=? ORDER BY priority, name",
            (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_active_text_models(store_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM models WHERE store_id=? AND model_type='text' AND is_active=1 "
            "ORDER BY (provider='openai'), priority, name",
            (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_active_image_models(store_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM models WHERE store_id=? AND model_type='image' AND is_active=1 "
            "ORDER BY (provider='openai'), priority, name",
            (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_model(model_id: str) -> Optional[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM models WHERE id=?", (model_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_model(model: dict) -> str:
    if not model.get("id"):
        model["id"] = str(uuid.uuid4())
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO models
                 (id, store_id, name, provider, model_type, model_name,
                  api_key, endpoint, extra_json, priority, is_active)
               VALUES
                 (:id, :store_id, :name, :provider, :model_type, :model_name,
                  :api_key, :endpoint, :extra_json, :priority, :is_active)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 provider=excluded.provider,
                 model_type=excluded.model_type,
                 model_name=excluded.model_name,
                 api_key=excluded.api_key,
                 endpoint=excluded.endpoint,
                 extra_json=excluded.extra_json,
                 priority=excluded.priority,
                 is_active=excluded.is_active""",
            model,
        )
        await db.commit()
    return model["id"]


async def delete_model(model_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM models WHERE id=?", (model_id,))
        await db.commit()


async def set_model_active(model_id: str, is_active: bool) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE models SET is_active=? WHERE id=?",
            (1 if is_active else 0, model_id),
        )
        await db.commit()
