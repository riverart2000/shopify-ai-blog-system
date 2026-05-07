"""db/base.py — Connection management, schema creation, and migrations.

Schema version history:
  v1: Original tables (settings, stores, prompts, access_tokens, generations)
  v2: Add store_settings, models, scheduled_jobs, generation_errors;
      add password_hash to stores; add store_id to prompts;
      migrate global settings → per-store settings + model records.
    v3: Add is_product_blog to scheduled_jobs.
    v4: Add content_text to generations for local similarity checks.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("ai_blog_server")

_DB_PATH: str = "data/ai_blog_server.db"


def set_db_path(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> str:
    return _DB_PATH


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stores (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    myshopify_domain    TEXT NOT NULL,
    custom_domain       TEXT NOT NULL DEFAULT '',
    client_id           TEXT NOT NULL DEFAULT '',
    client_secret       TEXT NOT NULL DEFAULT '',
    default_blog_handle TEXT NOT NULL DEFAULT 'news',
    default_author      TEXT NOT NULL DEFAULT 'Store Team',
    sort_order          INTEGER NOT NULL DEFAULT 0,
    password_hash       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS store_settings (
    store_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (store_id, key)
);

CREATE TABLE IF NOT EXISTS models (
    id         TEXT PRIMARY KEY,
    store_id   TEXT NOT NULL,
    name       TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model_type TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    api_key    TEXT NOT NULL DEFAULT '',
    endpoint   TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}',
    priority   INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS prompts (
    id         TEXT PRIMARY KEY,
    store_id   TEXT NOT NULL DEFAULT '',
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
    content_text TEXT NOT NULL DEFAULT '',
    keywords    TEXT NOT NULL DEFAULT '[]',
    hashtags    TEXT NOT NULL DEFAULT '[]',
    image_count INTEGER NOT NULL DEFAULT 0,
    article_id  TEXT,
    article_url TEXT,
    status      TEXT NOT NULL DEFAULT 'published',
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS generation_errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   TEXT NOT NULL,
    model_id   TEXT,
    provider   TEXT,
    error_type TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id          TEXT PRIMARY KEY,
    store_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    prompt_id   TEXT NOT NULL,
    blog_handle TEXT NOT NULL DEFAULT 'news',
    author      TEXT NOT NULL DEFAULT '',
    cron_expr   TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    is_active   INTEGER NOT NULL DEFAULT 1,
    last_run_at INTEGER,
    next_run_at INTEGER,
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS keyword_pool (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(store_id, keyword)
);

CREATE TABLE IF NOT EXISTS blog_title_pool (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id         TEXT NOT NULL,
    title            TEXT NOT NULL,
    keyword          TEXT NOT NULL DEFAULT '',
    search_intent    TEXT NOT NULL DEFAULT '',
    meta_description TEXT NOT NULL DEFAULT '',
    used             INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(store_id, title)
);
"""

# Columns added in v2 — wrapped in try/except since ALTER TABLE has no IF NOT EXISTS
_V2_COLUMNS = [
    "ALTER TABLE stores ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE prompts ADD COLUMN store_id TEXT NOT NULL DEFAULT ''",
]

_V3_COLUMNS = [
    "ALTER TABLE scheduled_jobs ADD COLUMN is_product_blog INTEGER NOT NULL DEFAULT 0",
]

_V4_COLUMNS = [
    "ALTER TABLE generations ADD COLUMN content_text TEXT NOT NULL DEFAULT ''",
]

_V5_COLUMNS = [
    "ALTER TABLE stores ADD COLUMN custom_domain TEXT NOT NULL DEFAULT ''",
]

_V6_COLUMNS = [
    "ALTER TABLE scheduled_jobs ADD COLUMN use_keyword_pool INTEGER NOT NULL DEFAULT 0",
]

_V7_COLUMNS = [
    "ALTER TABLE keyword_pool ADD COLUMN source TEXT NOT NULL DEFAULT ''",
]

_V8_COLUMNS = [
    "ALTER TABLE keyword_pool ADD COLUMN content TEXT NOT NULL DEFAULT ''",
]

_V9_COLUMNS = [
    "ALTER TABLE blog_title_pool ADD COLUMN used_at INTEGER",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables and run migrations. Idempotent — safe on every startup."""
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)

        for stmt in _V2_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V3_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V4_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V5_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V6_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V7_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V8_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        for stmt in _V9_COLUMNS:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # column already exists

        await _migrate_v1_to_v2(db)
        await db.commit()

    logger.info("Database initialised at %s", _DB_PATH)


async def get_admin_password_hash() -> Optional[str]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='admin_password_hash'"
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def set_admin_password_hash(hashed: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
            (hashed,),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """
    One-time: copy global settings → store_settings for the first store,
    create default model records from deepseek/grok settings,
    and rename the legacy password_hash key to admin_password_hash.
    Skipped if flag '_v2_migrated' is present in settings.
    """
    async with db.execute(
        "SELECT value FROM settings WHERE key='_v2_migrated'"
    ) as cur:
        if await cur.fetchone():
            return  # already done

    # ── find first store ────────────────────────────────────────────────────
    async with db.execute(
        "SELECT id FROM stores ORDER BY sort_order, name LIMIT 1"
    ) as cur:
        store_row = await cur.fetchone()

    if not store_row:
        # No stores yet — will migrate on first store creation
        return

    first_store_id = store_row[0]

    # ── copy global settings → store_settings ───────────────────────────────
    async with db.execute(
        "SELECT key, value FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'"
    ) as cur:
        raw = await cur.fetchall()
    s: dict[str, str] = {k: v for k, v in raw}

    for key, value in s.items():
        await db.execute(
            "INSERT OR IGNORE INTO store_settings (store_id, key, value) VALUES (?,?,?)",
            (first_store_id, key, value),
        )

    # ── create default model records ────────────────────────────────────────
    async with db.execute(
        "SELECT COUNT(*) FROM models WHERE store_id=?", (first_store_id,)
    ) as cur:
        if (await cur.fetchone())[0] == 0:

            deepseek_key = s.get("deepseek_api_key", "")
            if deepseek_key or s.get("deepseek_model"):
                await db.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id,store_id,name,provider,model_type,model_name,api_key,endpoint,extra_json,priority,is_active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), first_store_id,
                        "DeepSeek Chat", "deepseek", "text",
                        s.get("deepseek_model", "deepseek-chat"),
                        deepseek_key,
                        s.get("deepseek_endpoint", "https://api.deepseek.com/chat/completions"),
                        json.dumps({
                            "temperature": float(s.get("deepseek_temperature", "0.7")),
                            "timeout": int(s.get("deepseek_timeout", "90")),
                            "max_retries": int(s.get("deepseek_max_retries", "2")),
                            "system_prompt": s.get("deepseek_system_prompt", ""),
                        }),
                        0, 1,
                    ),
                )

            grok_key = s.get("grok_api_key", "")
            if grok_key or s.get("grok_model"):
                await db.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id,store_id,name,provider,model_type,model_name,api_key,endpoint,extra_json,priority,is_active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), first_store_id,
                        "Grok Image", "grok", "image",
                        s.get("grok_model", "grok-2-image"),
                        grok_key,
                        s.get("grok_endpoint", "https://api.x.ai/v1/images/generations"),
                        json.dumps({
                            "image_count": int(s.get("grok_image_count", "2")),
                            "timeout": int(s.get("grok_timeout", "60")),
                        }),
                        0, 1,
                    ),
                )

    # ── migrate old password_hash → admin_password_hash ─────────────────────
    async with db.execute(
        "SELECT value FROM settings WHERE key='password_hash'"
    ) as cur:
        pw_row = await cur.fetchone()
    if pw_row and pw_row[0]:
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
            (pw_row[0],),
        )

    # ── assign legacy prompts to first store ────────────────────────────────
    await db.execute(
        "UPDATE prompts SET store_id=? WHERE store_id=''",
        (first_store_id,),
    )

    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('_v2_migrated', '1')"
    )
    logger.info("DB migration v1→v2 complete (first_store=%s)", first_store_id)
