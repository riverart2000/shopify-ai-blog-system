"""Versioned schema for the Social Engine.

Each migration runs once, in order, inside its own transaction, and the applied
version is recorded in `social_engine_meta`. To change the schema, append a new
`Migration` to `MIGRATIONS` — never edit one that has already shipped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger("ai_blog_server.social_engine")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


_V1_CORE = """
CREATE TABLE IF NOT EXISTS social_products (
    store_id            TEXT NOT NULL,
    product_id          TEXT NOT NULL,
    gid                 TEXT NOT NULL DEFAULT '',
    handle              TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    product_type        TEXT NOT NULL DEFAULT '',
    vendor              TEXT NOT NULL DEFAULT '',
    tags_json           TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    url                 TEXT NOT NULL DEFAULT '',
    featured_image_url  TEXT NOT NULL DEFAULT '',
    images_json         TEXT NOT NULL DEFAULT '[]',
    variants_json       TEXT NOT NULL DEFAULT '[]',
    collections_json    TEXT NOT NULL DEFAULT '[]',
    price_min           REAL NOT NULL DEFAULT 0,
    price_max           REAL NOT NULL DEFAULT 0,
    compare_at_max      REAL,
    currency            TEXT NOT NULL DEFAULT 'GBP',
    on_sale             INTEGER NOT NULL DEFAULT 0,
    discount_pct        REAL NOT NULL DEFAULT 0,
    total_inventory     INTEGER,
    available           INTEGER NOT NULL DEFAULT 0,
    shopify_created_at  TEXT NOT NULL DEFAULT '',
    shopify_updated_at  TEXT NOT NULL DEFAULT '',
    published_at        TEXT NOT NULL DEFAULT '',
    content_hash        TEXT NOT NULL DEFAULT '',
    first_seen_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    synced_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    deleted_at          INTEGER,
    last_posted_at      INTEGER,
    post_count          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (store_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_social_products_available
    ON social_products(store_id, deleted_at, available, status);
CREATE INDEX IF NOT EXISTS idx_social_products_rotation
    ON social_products(store_id, last_posted_at);
CREATE INDEX IF NOT EXISTS idx_social_products_handle
    ON social_products(store_id, handle);

CREATE TABLE IF NOT EXISTS social_collections (
    store_id          TEXT NOT NULL,
    collection_id     TEXT NOT NULL,
    handle            TEXT NOT NULL DEFAULT '',
    title             TEXT NOT NULL DEFAULT '',
    image_url         TEXT NOT NULL DEFAULT '',
    products_count    INTEGER NOT NULL DEFAULT 0,
    product_ids_json  TEXT NOT NULL DEFAULT '[]',
    synced_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (store_id, collection_id)
);

CREATE TABLE IF NOT EXISTS social_product_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id      TEXT NOT NULL,
    product_id    TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    details_json  TEXT NOT NULL DEFAULT '{}',
    source        TEXT NOT NULL DEFAULT 'sync',
    detected_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_product_events_type
    ON social_product_events(store_id, event_type, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_product_events_product
    ON social_product_events(store_id, product_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS social_channels (
    store_id              TEXT NOT NULL,
    channel_id            TEXT NOT NULL,
    provider              TEXT NOT NULL,
    name                  TEXT NOT NULL DEFAULT '',
    profile               TEXT NOT NULL DEFAULT '',
    picture               TEXT NOT NULL DEFAULT '',
    disabled              INTEGER NOT NULL DEFAULT 0,
    customer_id           TEXT NOT NULL DEFAULT '',
    customer_name         TEXT NOT NULL DEFAULT '',
    enabled               INTEGER NOT NULL DEFAULT 1,
    max_length            INTEGER,
    rules                 TEXT NOT NULL DEFAULT '',
    settings_schema_json  TEXT NOT NULL DEFAULT '{}',
    default_settings_json TEXT NOT NULL DEFAULT '{}',
    cadence_json          TEXT NOT NULL DEFAULT '{}',
    missing_at            INTEGER,
    last_synced_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (store_id, channel_id)
);

CREATE TABLE IF NOT EXISTS social_templates (
    id               TEXT PRIMARY KEY,
    store_id         TEXT NOT NULL,
    key              TEXT NOT NULL,
    name             TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'image',
    formats_json     TEXT NOT NULL DEFAULT '[]',
    spec_json        TEXT NOT NULL DEFAULT '{}',
    caption_template TEXT NOT NULL DEFAULT '',
    is_builtin       INTEGER NOT NULL DEFAULT 0,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(store_id, key)
);

CREATE TABLE IF NOT EXISTS social_campaigns (
    id                   TEXT PRIMARY KEY,
    store_id             TEXT NOT NULL,
    name                 TEXT NOT NULL,
    rule_type            TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'paused',
    config_json          TEXT NOT NULL DEFAULT '{}',
    product_filter_json  TEXT NOT NULL DEFAULT '{}',
    channel_ids_json     TEXT NOT NULL DEFAULT '[]',
    content_json         TEXT NOT NULL DEFAULT '{}',
    caption_json         TEXT NOT NULL DEFAULT '{}',
    approval_mode        TEXT NOT NULL DEFAULT 'manual',
    last_evaluated_at    INTEGER,
    next_evaluation_at   INTEGER,
    last_error           TEXT NOT NULL DEFAULT '',
    created_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at           INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_campaigns_due
    ON social_campaigns(status, next_evaluation_at);

CREATE TABLE IF NOT EXISTS social_planned_posts (
    id                TEXT PRIMARY KEY,
    store_id          TEXT NOT NULL,
    campaign_id       TEXT NOT NULL DEFAULT '',
    rule_type         TEXT NOT NULL DEFAULT 'manual',
    content_type      TEXT NOT NULL,
    template_key      TEXT NOT NULL DEFAULT '',
    product_ids_json  TEXT NOT NULL DEFAULT '[]',
    title             TEXT NOT NULL DEFAULT '',
    angle             TEXT NOT NULL DEFAULT '',
    brief             TEXT NOT NULL DEFAULT '',
    base_caption      TEXT NOT NULL DEFAULT '',
    hashtags_json     TEXT NOT NULL DEFAULT '[]',
    alt_text          TEXT NOT NULL DEFAULT '',
    link_url          TEXT NOT NULL DEFAULT '',
    options_json      TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'planned',
    approval_mode     TEXT NOT NULL DEFAULT 'manual',
    publish_mode      TEXT NOT NULL DEFAULT 'schedule',
    scheduled_at      INTEGER,
    dedupe_key        TEXT NOT NULL DEFAULT '',
    external_group    TEXT NOT NULL DEFAULT '',
    error_type        TEXT NOT NULL DEFAULT '',
    error_message     TEXT NOT NULL DEFAULT '',
    generation_json   TEXT NOT NULL DEFAULT '{}',
    review_json       TEXT NOT NULL DEFAULT '{}',
    approved_at       INTEGER,
    approved_by       TEXT NOT NULL DEFAULT '',
    rejected_reason   TEXT NOT NULL DEFAULT '',
    published_at      INTEGER,
    created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_planned_posts_status
    ON social_planned_posts(store_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_social_planned_posts_campaign
    ON social_planned_posts(campaign_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_planned_posts_dedupe
    ON social_planned_posts(store_id, dedupe_key) WHERE dedupe_key != '';

CREATE TABLE IF NOT EXISTS social_post_targets (
    id               TEXT PRIMARY KEY,
    post_id          TEXT NOT NULL,
    store_id         TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    provider         TEXT NOT NULL,
    caption          TEXT NOT NULL DEFAULT '',
    first_comment    TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    settings_json    TEXT NOT NULL DEFAULT '{}',
    media_ids_json   TEXT NOT NULL DEFAULT '[]',
    format           TEXT NOT NULL DEFAULT '',
    link_url         TEXT NOT NULL DEFAULT '',
    scheduled_at     INTEGER,
    status           TEXT NOT NULL DEFAULT 'pending',
    external_id      TEXT NOT NULL DEFAULT '',
    external_group   TEXT NOT NULL DEFAULT '',
    release_url      TEXT NOT NULL DEFAULT '',
    error_type       TEXT NOT NULL DEFAULT '',
    error_message    TEXT NOT NULL DEFAULT '',
    published_at     INTEGER,
    last_synced_at   INTEGER,
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(post_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_social_post_targets_schedule
    ON social_post_targets(store_id, channel_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_social_post_targets_external
    ON social_post_targets(external_id);

CREATE TABLE IF NOT EXISTS social_media_assets (
    id             TEXT PRIMARY KEY,
    store_id       TEXT NOT NULL,
    post_id        TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT 'image',
    format         TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL DEFAULT 'primary',
    position       INTEGER NOT NULL DEFAULT 0,
    path           TEXT NOT NULL,
    mime           TEXT NOT NULL DEFAULT '',
    width          INTEGER NOT NULL DEFAULT 0,
    height         INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    bytes          INTEGER NOT NULL DEFAULT 0,
    sha256         TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    source_ref     TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL DEFAULT '',
    external_path  TEXT NOT NULL DEFAULT '',
    uploaded_at    INTEGER,
    created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_media_assets_post
    ON social_media_assets(post_id, role, position);
CREATE INDEX IF NOT EXISTS idx_social_media_assets_sha
    ON social_media_assets(store_id, sha256);

CREATE TABLE IF NOT EXISTS social_jobs (
    id                TEXT PRIMARY KEY,
    store_id          TEXT NOT NULL DEFAULT '',
    type              TEXT NOT NULL,
    payload_json      TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'queued',
    priority          INTEGER NOT NULL DEFAULT 0,
    run_at            INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    lease_owner       TEXT NOT NULL DEFAULT '',
    lease_expires_at  INTEGER,
    last_error_type   TEXT NOT NULL DEFAULT '',
    last_error        TEXT NOT NULL DEFAULT '',
    result_json       TEXT NOT NULL DEFAULT '{}',
    dedupe_key        TEXT NOT NULL DEFAULT '',
    correlation_id    TEXT NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    started_at        INTEGER,
    finished_at       INTEGER,
    updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_jobs_due
    ON social_jobs(status, run_at, priority DESC);
CREATE INDEX IF NOT EXISTS idx_social_jobs_correlation
    ON social_jobs(correlation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_jobs_type
    ON social_jobs(type, status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_jobs_active_dedupe
    ON social_jobs(dedupe_key)
    WHERE dedupe_key != '' AND status IN ('queued','running');

CREATE TABLE IF NOT EXISTS social_activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    store_id        TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    level           TEXT NOT NULL DEFAULT 'info',
    stage           TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL,
    data_json       TEXT NOT NULL DEFAULT '{}',
    correlation_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_social_activity_entity
    ON social_activity(entity_type, entity_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_social_activity_store
    ON social_activity(store_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_activity_level
    ON social_activity(store_id, level, created_at DESC);

CREATE TABLE IF NOT EXISTS social_api_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    store_id          TEXT NOT NULL DEFAULT '',
    service           TEXT NOT NULL,
    operation         TEXT NOT NULL DEFAULT '',
    method            TEXT NOT NULL DEFAULT '',
    url_path          TEXT NOT NULL DEFAULT '',
    status_code       INTEGER,
    ok                INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    attempt           INTEGER NOT NULL DEFAULT 1,
    error             TEXT NOT NULL DEFAULT '',
    correlation_id    TEXT NOT NULL DEFAULT '',
    request_excerpt   TEXT NOT NULL DEFAULT '',
    response_excerpt  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_social_api_calls_service
    ON social_api_calls(service, operation, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_api_calls_correlation
    ON social_api_calls(correlation_id, id DESC);

CREATE TABLE IF NOT EXISTS social_webhook_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id      TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL,
    topic         TEXT NOT NULL DEFAULT '',
    dedupe_key    TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'received',
    error         TEXT NOT NULL DEFAULT '',
    received_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    processed_at  INTEGER,
    UNIQUE(source, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_social_webhook_events_received
    ON social_webhook_events(source, received_at DESC);

CREATE TABLE IF NOT EXISTS social_post_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id      TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    channel_id    TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    external_id   TEXT NOT NULL DEFAULT '',
    period_days   INTEGER NOT NULL DEFAULT 7,
    impressions   INTEGER NOT NULL DEFAULT 0,
    reach         INTEGER NOT NULL DEFAULT 0,
    views         INTEGER NOT NULL DEFAULT 0,
    likes         INTEGER NOT NULL DEFAULT 0,
    comments      INTEGER NOT NULL DEFAULT 0,
    shares        INTEGER NOT NULL DEFAULT 0,
    saves         INTEGER NOT NULL DEFAULT 0,
    clicks        INTEGER NOT NULL DEFAULT 0,
    engagement    INTEGER NOT NULL DEFAULT 0,
    raw_json      TEXT NOT NULL DEFAULT '[]',
    fetched_at    INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_social_post_metrics_target
    ON social_post_metrics(target_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_post_metrics_post
    ON social_post_metrics(post_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS social_channel_metrics (
    store_id     TEXT NOT NULL,
    channel_id   TEXT NOT NULL,
    metric       TEXT NOT NULL,
    day          TEXT NOT NULL,
    value        REAL NOT NULL DEFAULT 0,
    fetched_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (store_id, channel_id, metric, day)
);

CREATE TABLE IF NOT EXISTS social_decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    store_id       TEXT NOT NULL DEFAULT '',
    decision_type  TEXT NOT NULL,
    subject_id     TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    valid          INTEGER NOT NULL DEFAULT 0,
    used_fallback  INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    input_json     TEXT NOT NULL DEFAULT '{}',
    output_json    TEXT NOT NULL DEFAULT '{}',
    error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_social_decisions_subject
    ON social_decisions(subject_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_decisions_type
    ON social_decisions(store_id, decision_type, created_at DESC);

CREATE TABLE IF NOT EXISTS social_engine_state (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL DEFAULT '{}',
    updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "core tables", _V1_CORE),
)

LATEST_VERSION = MIGRATIONS[-1].version


async def ensure_schema(conn: aiosqlite.Connection) -> int:
    """Apply pending migrations on an open connection. Returns the schema version."""
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS social_engine_meta (
               key   TEXT PRIMARY KEY,
               value TEXT NOT NULL DEFAULT ''
           )"""
    )
    async with conn.execute(
        "SELECT value FROM social_engine_meta WHERE key='schema_version'"
    ) as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row and str(row[0]).isdigit() else 0

    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        logger.info(
            "Applying Social Engine migration v%d (%s)", migration.version, migration.name,
        )
        await conn.executescript(migration.sql)
        await conn.execute(
            "INSERT INTO social_engine_meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(migration.version),),
        )
        await conn.commit()
        current = migration.version
    return current
