"""
database.py — SQLite database: full config storage + runtime state.

Tables:
  settings        — key/value store for API keys, server settings
  stores          — Shopify store configs
  prompts         — prompt templates
  access_tokens   — cached Shopify OAuth tokens per store (with expiry)
  generations     — history of every blog generated and published
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiosqlite

if TYPE_CHECKING:
    from config import AppConfig

logger = logging.getLogger("ai_blog_server")

_DB_PATH: str = "data/ai_blog_server.db"


def set_db_path(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path


async def init_db() -> None:
    """Create all tables if they don't exist."""
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS stores (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                myshopify_domain    TEXT NOT NULL,
                custom_domain       TEXT NOT NULL DEFAULT '',
                client_id           TEXT NOT NULL,
                client_secret       TEXT NOT NULL,
                default_blog_handle TEXT NOT NULL DEFAULT 'news',
                default_author      TEXT NOT NULL DEFAULT 'Store Team',
                sort_order          INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS prompts (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                text       TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS access_tokens (
                store_id   TEXT PRIMARY KEY,
                token      TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id    TEXT NOT NULL,
                store_name  TEXT NOT NULL,
                blog_handle TEXT NOT NULL,
                prompt_id   TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                title       TEXT NOT NULL,
                summary     TEXT NOT NULL,
                keywords    TEXT NOT NULL,
                hashtags    TEXT NOT NULL,
                image_count INTEGER NOT NULL DEFAULT 0,
                article_id  TEXT,
                article_url TEXT,
                status      TEXT NOT NULL DEFAULT 'published',
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
        """)
        await db.commit()
        # Migrate: add columns that didn't exist in older schema
        try:
            await db.execute("ALTER TABLE stores ADD COLUMN custom_domain TEXT NOT NULL DEFAULT ''")
            await db.commit()
            logger.info("Migration: added stores.custom_domain column")
        except Exception as _e:
            if "duplicate column" not in str(_e).lower():
                logger.warning("Migration stores.custom_domain: %s", _e)
    logger.info("Database initialised at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# Settings (key/value)
# ---------------------------------------------------------------------------

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else default


async def set_settings(pairs: dict[str, str]) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        for key, value in pairs.items():
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        await db.commit()


async def get_all_settings() -> dict[str, str]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

async def get_stores() -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM stores ORDER BY sort_order, name") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_store_row(store_id: str) -> Optional[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM stores WHERE id = ?", (store_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_store(store: dict) -> None:
    store = {**store, "custom_domain": store.get("custom_domain", "")}
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT INTO stores
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
                sort_order=excluded.sort_order
        """, store)
        await db.commit()


async def delete_store(store_id: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM stores WHERE id = ?", (store_id,))
        await db.execute("DELETE FROM access_tokens WHERE store_id = ?", (store_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

async def get_prompts() -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM prompts ORDER BY sort_order, name") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def upsert_prompt(prompt: dict) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT INTO prompts (id, name, text, sort_order)
            VALUES (:id, :name, :text, :sort_order)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                text=excluded.text,
                sort_order=excluded.sort_order
        """, prompt)
        await db.commit()


async def delete_prompt(prompt_id: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# Seed from initial config.json (first run only)
# ---------------------------------------------------------------------------

async def is_seeded() -> bool:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM settings") as cur:
            count = (await cur.fetchone())[0]
    return count > 0


async def seed_from_config(config: "AppConfig") -> None:
    """Populate DB from AppConfig on first run. No-op if already seeded."""
    if await is_seeded():
        return

    await set_settings({
        "deepseek_api_key":       config.deepseek.api_key,
        "deepseek_endpoint":      config.deepseek.endpoint,
        "deepseek_model":         config.deepseek.model,
        "deepseek_temperature":   str(config.deepseek.temperature),
        "deepseek_timeout":       str(config.deepseek.timeout_seconds),
        "deepseek_max_retries":   str(config.deepseek.max_retries),
        "deepseek_system_prompt": config.deepseek.system_prompt,
        "grok_api_key":           config.grok.api_key,
        "grok_endpoint":          config.grok.endpoint,
        "grok_model":             config.grok.model,
        "grok_image_count":       str(config.grok.image_count),
        "grok_timeout":           str(config.grok.timeout_seconds),
        "server_mode":            config.server.mode,
        "server_secret_key":      config.server.secret_key,
    })

    for i, store in enumerate(config.stores):
        await upsert_store({
            "id": store.id, "name": store.name,
            "myshopify_domain": store.myshopify_domain,
            "client_id": store.client_id, "client_secret": store.client_secret,
            "default_blog_handle": store.default_blog_handle,
            "default_author": store.default_author, "sort_order": i,
        })

    for i, prompt in enumerate(config.prompts):
        await upsert_prompt({
            "id": prompt.id, "name": prompt.name,
            "text": prompt.text, "sort_order": i,
        })

    logger.info("Database seeded from bootstrap config")


# ---------------------------------------------------------------------------
# Load runtime config from DB
# ---------------------------------------------------------------------------

async def load_runtime_config(bootstrap: "AppConfig") -> "AppConfig":
    """Build a live AppConfig from DB values. Host/port/logging stay from bootstrap."""
    from config import (
        AppConfig, ServerConfig, DeepSeekConfig, GrokConfig, StoreConfig, PromptConfig,
    )
    s = await get_all_settings()

    server = ServerConfig(
        host=bootstrap.server.host,
        port=bootstrap.server.port,
        mode=s.get("server_mode", bootstrap.server.mode),
        secret_key=s.get("server_secret_key", bootstrap.server.secret_key),
    )
    deepseek = DeepSeekConfig(
        api_key=s.get("deepseek_api_key", ""),
        endpoint=s.get("deepseek_endpoint", "https://api.deepseek.com/chat/completions"),
        model=s.get("deepseek_model", "deepseek-chat"),
        temperature=float(s.get("deepseek_temperature", "0.7")),
        timeout_seconds=int(s.get("deepseek_timeout", "90")),
        max_retries=int(s.get("deepseek_max_retries", "2")),
        system_prompt=s.get("deepseek_system_prompt", ""),
    )
    grok = GrokConfig(
        api_key=s.get("grok_api_key", ""),
        endpoint=s.get("grok_endpoint", "https://api.x.ai/v1/images/generations"),
        model=s.get("grok_model", "grok-2-image"),
        image_count=int(s.get("grok_image_count", "2")),
        timeout_seconds=int(s.get("grok_timeout", "60")),
    )
    stores = [
        StoreConfig(
            id=r["id"], name=r["name"],
            myshopify_domain=r["myshopify_domain"],
            custom_domain=r.get("custom_domain", ""),
            client_id=r["client_id"], client_secret=r["client_secret"],
            default_blog_handle=r["default_blog_handle"],
            default_author=r["default_author"],
        )
        for r in await get_stores()
    ]
    prompts = [
        PromptConfig(id=r["id"], name=r["name"], text=r["text"])
        for r in await get_prompts()
    ]
    return AppConfig(
        server=server, logging=bootstrap.logging,
        deepseek=deepseek, grok=grok, stores=stores, prompts=prompts,
        default_prompt_id=s.get("default_prompt_id", ""),
    )


# ---------------------------------------------------------------------------
# Access token cache
# ---------------------------------------------------------------------------

async def get_cached_token(store_id: str) -> Optional[str]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            "SELECT token, expires_at FROM access_tokens WHERE store_id = ?", (store_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    token, expires_at = row
    if int(time.time()) >= expires_at:
        logger.debug("Cached token for store %s has expired", store_id)
        return None
    return token


async def save_token(store_id: str, token: str, expires_at: int) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT INTO access_tokens (store_id, token, expires_at) VALUES (?,?,?) "
            "ON CONFLICT(store_id) DO UPDATE SET token=excluded.token, expires_at=excluded.expires_at",
            (store_id, token, expires_at),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Generation history
# ---------------------------------------------------------------------------

async def log_generation(
    store_id: str, store_name: str, blog_handle: str,
    prompt_id: str, prompt_text: str, title: str, summary: str,
    keywords: list[str], hashtags: list[str], image_count: int,
    article_id: Optional[str], article_url: Optional[str],
    status: str = "published",
) -> int:
    async with aiosqlite.connect(_DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO generations
               (store_id, store_name, blog_handle, prompt_id, prompt_text,
                title, summary, keywords, hashtags, image_count,
                article_id, article_url, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                store_id, store_name, blog_handle, prompt_id, prompt_text,
                title, summary,
                json.dumps(keywords), json.dumps(hashtags),
                image_count, str(article_id) if article_id else None,
                article_url, status,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_recent_generations(limit: int = 50) -> list[dict]:
    rows = []
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM generations ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cursor:
            async for row in cursor:
                r = dict(row)
                r["keywords"] = json.loads(r["keywords"] or "[]")
                r["hashtags"] = json.loads(r["hashtags"] or "[]")
                rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# Admin password
# ---------------------------------------------------------------------------

async def get_password_hash() -> Optional[str]:
    """Return the stored bcrypt password hash, or None if not yet set."""
    val = await get_setting("admin_password_hash", "")
    return val if val else None


async def set_password_hash(hashed: str) -> None:
    await set_settings({"admin_password_hash": hashed})
