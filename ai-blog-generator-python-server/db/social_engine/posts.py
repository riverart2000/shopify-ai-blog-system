"""Planned posts, per-channel targets, rendered media assets and metrics.

Status changes go through `transition_post` / `transition_target`, which only
apply when the row is still in an expected state. That makes every pipeline
step idempotent and safe when the API and worker race on the same post.
"""
from __future__ import annotations

from typing import Any, Iterable

from .connection import connect, dumps, new_id, now, row_to_dict, rows_to_dicts

_POST_JSON = {
    "product_ids": [], "hashtags": [], "options": {}, "generation": {}, "review": {},
}
_TARGET_JSON = {"settings": {}, "media_ids": []}

_POST_SCALARS = {
    "campaign_id", "rule_type", "content_type", "template_key", "title", "angle", "brief",
    "base_caption", "alt_text", "link_url", "status", "approval_mode", "publish_mode",
    "scheduled_at", "dedupe_key", "external_group", "error_type", "error_message",
    "approved_at", "approved_by", "rejected_reason", "published_at",
}
_POST_JSON_FIELDS = set(_POST_JSON)
_TARGET_SCALARS = {
    "caption", "first_comment", "title", "format", "link_url", "scheduled_at", "status",
    "external_id", "external_group", "release_url", "error_type", "error_message",
    "published_at", "last_synced_at", "provider",
}
_TARGET_JSON_FIELDS = set(_TARGET_JSON)

POST_STATUSES = (
    "planned", "generating", "pending_approval", "approved", "publishing", "scheduled",
    "published", "partially_published", "failed", "rejected", "cancelled",
)
TARGET_STATUSES = (
    "pending", "publishing", "uncertain", "draft", "scheduled", "published", "failed", "cancelled",
)


def _assignments(fields: dict, scalars: set[str], json_fields: set[str]) -> tuple[list[str], list[Any]]:
    sets: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in scalars:
            sets.append(f"{key}=?")
            params.append(value)
        elif key in json_fields:
            sets.append(f"{key}_json=?")
            params.append(dumps(value))
        else:
            raise ValueError(f"Unknown or read-only field: {key}")
    return sets, params


# ---------------------------------------------------------------------------
# Planned posts
# ---------------------------------------------------------------------------

async def create_post(store_id: str, post: dict) -> tuple[str, bool]:
    """Create a planned post. Returns (post_id, created).

    A non-empty `dedupe_key` makes creation idempotent: re-running a rule for
    the same product/day returns the existing post instead of a duplicate.
    """
    dedupe_key = post.get("dedupe_key", "") or ""
    if dedupe_key:
        existing = await get_post_by_dedupe(store_id, dedupe_key)
        if existing:
            return existing["id"], False
    post_id = post.get("id") or new_id("post")
    timestamp = now()
    async with connect() as conn:
        try:
            await conn.execute(
                """INSERT INTO social_planned_posts
                   (id, store_id, campaign_id, rule_type, content_type, template_key,
                    product_ids_json, title, angle, brief, base_caption, hashtags_json, alt_text,
                    link_url, options_json, status, approval_mode, publish_mode, scheduled_at,
                    dedupe_key, generation_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    post_id, store_id, post.get("campaign_id", ""), post.get("rule_type", "manual"),
                    post["content_type"], post.get("template_key", ""),
                    dumps([str(pid) for pid in post.get("product_ids") or []]),
                    post.get("title", ""), post.get("angle", ""), post.get("brief", ""),
                    post.get("base_caption", ""), dumps(post.get("hashtags") or []),
                    post.get("alt_text", ""), post.get("link_url", ""), dumps(post.get("options") or {}),
                    post.get("status", "planned"), post.get("approval_mode", "manual"),
                    post.get("publish_mode", "schedule"), post.get("scheduled_at"),
                    dedupe_key, dumps(post.get("generation") or {}), timestamp, timestamp,
                ),
            )
            await conn.commit()
        except Exception as exc:
            if dedupe_key and "UNIQUE" in str(exc).upper():
                existing = await get_post_by_dedupe(store_id, dedupe_key)
                if existing:
                    return existing["id"], False
            raise
    return post_id, True


async def get_post_by_dedupe(store_id: str, dedupe_key: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_planned_posts WHERE store_id=? AND dedupe_key=?", (store_id, dedupe_key),
        ) as cur:
            return row_to_dict(await cur.fetchone(), _POST_JSON)


async def list_posts_by_dedupe_prefix(store_id: str, prefix: str) -> list[dict]:
    """Lightweight rows (id, dedupe_key, status, content_type) for a key prefix."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    async with connect() as conn:
        async with conn.execute(
            "SELECT id, dedupe_key, status, content_type, scheduled_at FROM social_planned_posts "
            "WHERE store_id=? AND dedupe_key LIKE ? ESCAPE '\\'",
            (store_id, f"{escaped}%"),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


async def get_post(store_id: str, post_id: str, *, with_children: bool = True) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_planned_posts WHERE store_id=? AND id=?", (store_id, post_id),
        ) as cur:
            post = row_to_dict(await cur.fetchone(), _POST_JSON)
    if post and with_children:
        post["targets"] = await list_targets_for_post(post_id)
        post["media"] = await list_assets(post_id)
    return post


async def update_post(store_id: str, post_id: str, **fields: Any) -> bool:
    sets, params = _assignments(fields, _POST_SCALARS, _POST_JSON_FIELDS)
    if not sets:
        return False
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_planned_posts SET {', '.join(sets)}, updated_at=? WHERE store_id=? AND id=?",
            (*params, now(), store_id, post_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def transition_post(
    store_id: str, post_id: str, *, from_statuses: Iterable[str], to_status: str, **fields: Any,
) -> bool:
    """Change status only if the post is currently in one of `from_statuses`."""
    allowed = list(from_statuses)
    sets, params = _assignments(fields, _POST_SCALARS, _POST_JSON_FIELDS)
    sets.insert(0, "status=?")
    params.insert(0, to_status)
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_planned_posts SET {', '.join(sets)}, updated_at=? "
            f"WHERE store_id=? AND id=? AND status IN ({','.join('?' * len(allowed))})",
            (*params, now(), store_id, post_id, *allowed),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_posts(
    store_id: str,
    *,
    statuses: Iterable[str] = (),
    campaign_id: str = "",
    product_id: str = "",
    content_type: str = "",
    scheduled_from: int | None = None,
    scheduled_to: int | None = None,
    search: str = "",
    order: str = "scheduled",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = ["store_id=?"]
    params: list[Any] = [store_id]
    status_list = list(statuses)
    if status_list:
        clauses.append(f"status IN ({','.join('?' * len(status_list))})")
        params.extend(status_list)
    if campaign_id:
        clauses.append("campaign_id=?")
        params.append(campaign_id)
    if product_id:
        clauses.append("product_ids_json LIKE ?")
        params.append(f'%"{product_id}"%')
    if content_type:
        clauses.append("content_type=?")
        params.append(content_type)
    if scheduled_from is not None:
        clauses.append("scheduled_at>=?")
        params.append(scheduled_from)
    if scheduled_to is not None:
        clauses.append("scheduled_at<?")
        params.append(scheduled_to)
    if search:
        clauses.append("(title LIKE ? OR base_caption LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    order_sql = {
        "scheduled": "COALESCE(scheduled_at, 9999999999) ASC, created_at ASC",
        "newest": "created_at DESC",
        "updated": "updated_at DESC",
    }.get(order, "created_at DESC")
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_planned_posts WHERE {' AND '.join(clauses)} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 500)), max(0, offset)),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _POST_JSON)


async def count_posts_by_status(store_id: str) -> dict[str, int]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT status, COUNT(*) AS n FROM social_planned_posts WHERE store_id=? GROUP BY status",
            (store_id,),
        ) as cur:
            return {str(row["status"]): int(row["n"]) for row in await cur.fetchall()}


async def posts_needing_reconcile(limit: int = 100) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            """SELECT DISTINCT p.* FROM social_planned_posts p
               JOIN social_post_targets t ON t.post_id = p.id
               WHERE t.status IN ('scheduled','uncertain','publishing')
               ORDER BY COALESCE(t.scheduled_at, 0) ASC LIMIT ?""",
            (limit,),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _POST_JSON)


# ---------------------------------------------------------------------------
# Targets (one per channel)
# ---------------------------------------------------------------------------

async def replace_targets(store_id: str, post_id: str, targets: list[dict]) -> list[str]:
    """Replace all unpublished targets of a post. Published/scheduled ones are kept."""
    timestamp = now()
    ids: list[str] = []
    async with connect() as conn:
        await conn.execute(
            "DELETE FROM social_post_targets WHERE post_id=? AND status IN ('pending','failed','cancelled')",
            (post_id,),
        )
        for target in targets:
            target_id = target.get("id") or new_id("tgt")
            await conn.execute(
                """INSERT INTO social_post_targets
                   (id, post_id, store_id, channel_id, provider, caption, first_comment, title,
                    settings_json, media_ids_json, format, link_url, scheduled_at, status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)
                   ON CONFLICT(post_id, channel_id) DO NOTHING""",
                (
                    target_id, post_id, store_id, target["channel_id"], target.get("provider", ""),
                    target.get("caption", ""), target.get("first_comment", ""), target.get("title", ""),
                    dumps(target.get("settings") or {}), dumps(target.get("media_ids") or []),
                    target.get("format", ""), target.get("link_url", ""), target.get("scheduled_at"),
                    timestamp, timestamp,
                ),
            )
            ids.append(target_id)
        await conn.commit()
    return ids


async def list_targets_for_post(post_id: str) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_post_targets WHERE post_id=? ORDER BY provider, channel_id", (post_id,),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _TARGET_JSON)


async def get_target(target_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_post_targets WHERE id=?", (target_id,)) as cur:
            return row_to_dict(await cur.fetchone(), _TARGET_JSON)


async def update_target(target_id: str, **fields: Any) -> bool:
    sets, params = _assignments(fields, _TARGET_SCALARS, _TARGET_JSON_FIELDS)
    if not sets:
        return False
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_post_targets SET {', '.join(sets)}, updated_at=? WHERE id=?",
            (*params, now(), target_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def transition_target(
    target_id: str, *, from_statuses: Iterable[str], to_status: str, **fields: Any,
) -> bool:
    allowed = list(from_statuses)
    sets, params = _assignments(fields, _TARGET_SCALARS, _TARGET_JSON_FIELDS)
    sets.insert(0, "status=?")
    params.insert(0, to_status)
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_post_targets SET {', '.join(sets)}, updated_at=? "
            f"WHERE id=? AND status IN ({','.join('?' * len(allowed))})",
            (*params, now(), target_id, *allowed),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_targets(
    store_id: str,
    *,
    statuses: Iterable[str] = (),
    channel_id: str = "",
    scheduled_from: int | None = None,
    scheduled_to: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    clauses = ["t.store_id=?"]
    params: list[Any] = [store_id]
    status_list = list(statuses)
    if status_list:
        clauses.append(f"t.status IN ({','.join('?' * len(status_list))})")
        params.extend(status_list)
    if channel_id:
        clauses.append("t.channel_id=?")
        params.append(channel_id)
    if scheduled_from is not None:
        clauses.append("t.scheduled_at>=?")
        params.append(scheduled_from)
    if scheduled_to is not None:
        clauses.append("t.scheduled_at<?")
        params.append(scheduled_to)
    async with connect() as conn:
        async with conn.execute(
            f"""SELECT t.*, p.content_type, p.title AS post_title, p.status AS post_status,
                       p.campaign_id, p.product_ids_json, p.template_key
                FROM social_post_targets t JOIN social_planned_posts p ON p.id=t.post_id
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(t.scheduled_at, 0) ASC LIMIT ?""",
            (*params, max(1, min(limit, 5000))),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), {**_TARGET_JSON, "product_ids": []})


async def planned_channel_times(store_id: str, *, scheduled_from: int, scheduled_to: int) -> list[dict]:
    """Capacity held by posts that have no channel versions yet (planned/generating).

    Returned rows look like targets ({channel_id, scheduled_at, post_id, status})
    so the slot planner can count them against daily limits.
    """
    rows = await list_posts(store_id, statuses=("planned", "generating"), limit=1000)
    out: list[dict] = []
    for row in rows:
        for channel_id, ts in ((row.get("options") or {}).get("channel_times") or {}).items():
            if ts and scheduled_from <= int(ts) < scheduled_to:
                out.append({"channel_id": str(channel_id), "scheduled_at": int(ts), "post_id": row["id"],
                            "status": "planned", "provider": ""})
    return out


async def find_target_by_external_id(external_id: str) -> dict | None:
    if not external_id:
        return None
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_post_targets WHERE external_id=? LIMIT 1", (external_id,),
        ) as cur:
            return row_to_dict(await cur.fetchone(), _TARGET_JSON)


# ---------------------------------------------------------------------------
# Media assets
# ---------------------------------------------------------------------------

async def add_asset(store_id: str, asset: dict) -> str:
    asset_id = asset.get("id") or new_id("media")
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_media_assets
               (id, store_id, post_id, kind, format, role, position, path, mime, width, height,
                duration_ms, bytes, sha256, source, source_ref, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                asset_id, store_id, asset.get("post_id", ""), asset.get("kind", "image"),
                asset.get("format", ""), asset.get("role", "primary"), int(asset.get("position") or 0),
                asset["path"], asset.get("mime", ""), int(asset.get("width") or 0),
                int(asset.get("height") or 0), int(asset.get("duration_ms") or 0),
                int(asset.get("bytes") or 0), asset.get("sha256", ""), asset.get("source", ""),
                asset.get("source_ref", ""), now(),
            ),
        )
        await conn.commit()
    return asset_id


async def list_assets(post_id: str) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_media_assets WHERE post_id=? ORDER BY role, format, position", (post_id,),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


async def get_asset(asset_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_media_assets WHERE id=?", (asset_id,)) as cur:
            return row_to_dict(await cur.fetchone())


async def get_assets(asset_ids: Iterable[str]) -> list[dict]:
    ids = list(asset_ids)
    if not ids:
        return []
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_media_assets WHERE id IN ({','.join('?' * len(ids))})", ids,
        ) as cur:
            rows = rows_to_dicts(await cur.fetchall())
    by_id = {row["id"]: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


async def all_asset_paths() -> set[str]:
    async with connect() as conn:
        async with conn.execute("SELECT path FROM social_media_assets") as cur:
            return {str(row["path"]) for row in await cur.fetchall()}


async def set_asset_uploaded(asset_id: str, *, external_id: str, external_path: str) -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE social_media_assets SET external_id=?, external_path=?, uploaded_at=? WHERE id=?",
            (external_id, external_path, now(), asset_id),
        )
        await conn.commit()


async def detach_assets(post_id: str) -> list[dict]:
    """Remove asset rows for a post (before regeneration). Returns the removed rows."""
    rows = await list_assets(post_id)
    async with connect() as conn:
        await conn.execute("DELETE FROM social_media_assets WHERE post_id=?", (post_id,))
        await conn.commit()
    return rows


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

METRIC_COLUMNS = ("impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks", "engagement")


async def add_post_metrics(store_id: str, target: dict, metrics: dict, *, period_days: int, raw: Any) -> None:
    async with connect() as conn:
        await conn.execute(
            f"""INSERT INTO social_post_metrics
                (store_id, target_id, post_id, channel_id, provider, external_id, period_days,
                 {', '.join(METRIC_COLUMNS)}, raw_json, fetched_at)
                VALUES (?,?,?,?,?,?,?,{','.join('?' * len(METRIC_COLUMNS))},?,?)""",
            (
                store_id, target["id"], target["post_id"], target.get("channel_id", ""),
                target.get("provider", ""), target.get("external_id", ""), period_days,
                *[int(metrics.get(col) or 0) for col in METRIC_COLUMNS], dumps(raw), now(),
            ),
        )
        await conn.commit()


_LATEST_METRICS_CTE = """
WITH latest AS (
    SELECT m.* FROM social_post_metrics m
    JOIN (SELECT target_id, MAX(id) AS max_id FROM social_post_metrics WHERE store_id=? GROUP BY target_id) x
      ON x.max_id = m.id
)
"""


async def latest_metrics_for_post(store_id: str, post_id: str) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            _LATEST_METRICS_CTE + "SELECT * FROM latest WHERE post_id=?", (store_id, post_id),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), {"raw": []})


async def metric_rollup(store_id: str, *, group_by: str, since: int | None = None, limit: int = 100) -> list[dict]:
    """Aggregate the latest metrics snapshot per target by a dimension.

    group_by: channel | provider | content_type | template | campaign | product | hour
    """
    dims = {
        "channel": "latest.channel_id",
        "provider": "latest.provider",
        "content_type": "p.content_type",
        "template": "p.template_key",
        "campaign": "p.campaign_id",
        "hour": "CAST(strftime('%H', t.published_at, 'unixepoch', 'localtime') AS INTEGER)",
    }
    totals = ", ".join(f"SUM(latest.{col}) AS {col}" for col in METRIC_COLUMNS)
    since_clause = " AND COALESCE(t.published_at, p.created_at) >= ?" if since else ""
    params: list[Any] = [store_id, store_id]
    if since:
        params.append(since)
    if group_by == "product":
        sql = (
            _LATEST_METRICS_CTE
            + f"""SELECT je.value AS dimension, COUNT(DISTINCT latest.target_id) AS targets, {totals}
                  FROM latest JOIN social_post_targets t ON t.id = latest.target_id
                  JOIN social_planned_posts p ON p.id = latest.post_id, json_each(p.product_ids_json) je
                  WHERE p.store_id=?{since_clause}
                  GROUP BY je.value ORDER BY engagement DESC, impressions DESC LIMIT ?"""
        )
    else:
        if group_by not in dims:
            raise ValueError(f"Unsupported metric dimension: {group_by}")
        sql = (
            _LATEST_METRICS_CTE
            + f"""SELECT {dims[group_by]} AS dimension, COUNT(DISTINCT latest.target_id) AS targets, {totals}
                  FROM latest JOIN social_post_targets t ON t.id = latest.target_id
                  JOIN social_planned_posts p ON p.id = latest.post_id
                  WHERE p.store_id=?{since_clause}
                  GROUP BY dimension ORDER BY engagement DESC, impressions DESC LIMIT ?"""
        )
    params.append(max(1, min(limit, 1000)))
    async with connect() as conn:
        async with conn.execute(sql, params) as cur:
            return rows_to_dicts(await cur.fetchall())


async def top_posts(store_id: str, *, since: int | None = None, limit: int = 20) -> list[dict]:
    since_clause = " AND COALESCE(t.published_at, p.created_at) >= ?" if since else ""
    params: list[Any] = [store_id, store_id]
    if since:
        params.append(since)
    params.append(max(1, min(limit, 200)))
    totals = ", ".join(f"SUM(latest.{col}) AS {col}" for col in METRIC_COLUMNS)
    async with connect() as conn:
        async with conn.execute(
            _LATEST_METRICS_CTE
            + f"""SELECT p.id AS post_id, p.title, p.content_type, p.template_key, p.campaign_id,
                         p.product_ids_json, MIN(t.published_at) AS published_at, {totals}
                  FROM latest JOIN social_post_targets t ON t.id = latest.target_id
                  JOIN social_planned_posts p ON p.id = latest.post_id
                  WHERE p.store_id=?{since_clause}
                  GROUP BY p.id ORDER BY engagement DESC, impressions DESC LIMIT ?""",
            params,
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), {"product_ids": []})


async def targets_due_for_metrics(
    *, min_age_seconds: int, max_age_seconds: int, refresh_after_seconds: int, limit: int = 50,
) -> list[dict]:
    """Published targets aged [min_age, max_age] whose metrics are missing or stale."""
    timestamp = now()
    async with connect() as conn:
        async with conn.execute(
            """SELECT t.* FROM social_post_targets t
               LEFT JOIN (SELECT target_id, MAX(fetched_at) AS last_fetch FROM social_post_metrics GROUP BY target_id) m
                 ON m.target_id = t.id
               WHERE t.status='published' AND t.external_id != ''
                 AND t.published_at IS NOT NULL
                 AND t.published_at <= ? AND t.published_at >= ?
                 AND (m.last_fetch IS NULL OR m.last_fetch <= ?)
               ORDER BY COALESCE(m.last_fetch, 0) ASC LIMIT ?""",
            (
                timestamp - min_age_seconds, timestamp - max_age_seconds,
                timestamp - refresh_after_seconds, max(1, min(limit, 500)),
            ),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _TARGET_JSON)


async def upsert_channel_metric(store_id: str, channel_id: str, metric: str, day: str, value: float) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_channel_metrics(store_id, channel_id, metric, day, value, fetched_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(store_id, channel_id, metric, day) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at""",
            (store_id, channel_id, metric, day, float(value), now()),
        )
        await conn.commit()


async def list_channel_metrics(store_id: str, channel_id: str = "", *, since_day: str = "") -> list[dict]:
    clauses = ["store_id=?"]
    params: list[Any] = [store_id]
    if channel_id:
        clauses.append("channel_id=?")
        params.append(channel_id)
    if since_day:
        clauses.append("day>=?")
        params.append(since_day)
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_channel_metrics WHERE {' AND '.join(clauses)} ORDER BY channel_id, metric, day",
            params,
        ) as cur:
            return rows_to_dicts(await cur.fetchall())
