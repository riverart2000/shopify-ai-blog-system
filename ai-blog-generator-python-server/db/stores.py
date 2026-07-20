"""db/stores.py — Store CRUD, per-store settings, store passwords, token cache."""
from __future__ import annotations

import time
from typing import Optional

import aiosqlite

from .base import get_db_path


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

async def get_stores() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM stores ORDER BY sort_order, name"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_store(store_id: str) -> Optional[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM stores WHERE id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_store(store: dict) -> None:
    store = {**store, "custom_domain": store.get("custom_domain", "")}
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO stores
                 (id, name, myshopify_domain, custom_domain, client_id, client_secret,
                  default_blog_handle, default_author, sort_order)
               VALUES
                 (:id, :name, :myshopify_domain, :custom_domain, :client_id, :client_secret,
                  :default_blog_handle, :default_author, :sort_order)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 myshopify_domain=excluded.myshopify_domain,
                 custom_domain=excluded.custom_domain,
                 client_id=excluded.client_id,
                 client_secret=excluded.client_secret,
                 default_blog_handle=excluded.default_blog_handle,
                 default_author=excluded.default_author,
                 sort_order=excluded.sort_order""",
            store,
        )
        await db.commit()


async def delete_store(store_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        for tbl in (
            "stores", "access_tokens", "store_settings",
            "models", "prompts", "scheduled_jobs",
            "social_posts", "intelligence_runs", "intelligence_recommendations",
        ):
            col = "store_id" if tbl != "stores" else "id"
            await db.execute(f"DELETE FROM {tbl} WHERE {col}=?", (store_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Per-store passwords
# ---------------------------------------------------------------------------

async def get_store_password_hash(store_id: str) -> Optional[str]:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT password_hash FROM stores WHERE id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return row[0] if row[0] else None


async def set_store_password_hash(store_id: str, hashed: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE stores SET password_hash=? WHERE id=?", (hashed, store_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Per-store settings (key/value)
# ---------------------------------------------------------------------------

async def get_store_setting(store_id: str, key: str, default: str = "") -> str:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT value FROM store_settings WHERE store_id=? AND key=?",
            (store_id, key),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else default


async def set_store_settings(store_id: str, pairs: dict[str, str]) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        for key, value in pairs.items():
            await db.execute(
                "INSERT INTO store_settings (store_id,key,value) VALUES (?,?,?) "
                "ON CONFLICT(store_id,key) DO UPDATE SET value=excluded.value",
                (store_id, key, value),
            )
        await db.commit()


async def get_all_store_settings(store_id: str) -> dict[str, str]:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT key, value FROM store_settings WHERE store_id=?", (store_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Shopify token cache (per store)
# ---------------------------------------------------------------------------

async def get_cached_token(store_id: str) -> Optional[str]:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT token, expires_at FROM access_tokens WHERE store_id=?", (store_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    token, expires_at = row
    if int(time.time()) >= expires_at:
        return None
    return token


async def save_token(store_id: str, token: str, expires_at: int) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO access_tokens (store_id,token,expires_at) VALUES (?,?,?)",
            (store_id, token, expires_at),
        )
        await db.commit()
