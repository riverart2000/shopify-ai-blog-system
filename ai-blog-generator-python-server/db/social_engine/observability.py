"""Debugging records: activity timeline, external API calls, AI decisions,
webhook deliveries and small engine state values (heartbeats, cursors).

Everything written here is redacted and truncated so secrets and image data
never land in the database.
"""
from __future__ import annotations

from typing import Any

from services.system_events import _redact

from .connection import connect, dumps, loads, now, row_to_dict, rows_to_dicts

_EXCERPT_LIMIT = 2000


def _excerpt(value: Any, limit: int = _EXCERPT_LIMIT) -> str:
    if value is None or value == "":
        return ""
    text = value if isinstance(value, str) else dumps(value)
    return _redact(text, limit=limit)


# ---------------------------------------------------------------------------
# Activity timeline
# ---------------------------------------------------------------------------

async def add_activity(
    *,
    store_id: str,
    entity_type: str,
    entity_id: str,
    message: str,
    level: str = "info",
    stage: str = "",
    data: dict | None = None,
    correlation_id: str = "",
) -> int:
    async with connect() as conn:
        cur = await conn.execute(
            """INSERT INTO social_activity
               (created_at, store_id, entity_type, entity_id, level, stage, message,
                data_json, correlation_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                now(), store_id, entity_type, entity_id, level, stage,
                _redact(message, limit=2000), _excerpt(data or {}, 6000), correlation_id,
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def list_activity(
    *,
    store_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    correlation_id: str = "",
    level: str = "",
    limit: int = 100,
    before_id: int | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("store_id", store_id), ("entity_type", entity_type),
        ("entity_id", entity_id), ("correlation_id", correlation_id),
    ):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if level:
        levels = ["error"] if level == "error" else ["warning", "error"] if level == "warning" else [level]
        clauses.append(f"level IN ({','.join('?' * len(levels))})")
        params.extend(levels)
    if before_id:
        clauses.append("id<?")
        params.append(before_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_activity {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ) as cur:
            rows = rows_to_dicts(await cur.fetchall())
    for row in rows:
        row["data"] = loads(row.pop("data_json", "{}"), {})
    return rows


# ---------------------------------------------------------------------------
# External API call ledger
# ---------------------------------------------------------------------------

async def record_api_call(
    *,
    service: str,
    operation: str,
    method: str,
    url_path: str,
    status_code: int | None,
    ok: bool,
    duration_ms: int,
    attempt: int = 1,
    error: str = "",
    store_id: str = "",
    correlation_id: str = "",
    request_excerpt: Any = "",
    response_excerpt: Any = "",
) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_api_calls
               (created_at, store_id, service, operation, method, url_path, status_code,
                ok, duration_ms, attempt, error, correlation_id, request_excerpt, response_excerpt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now(), store_id, service, operation, method.upper(), _redact(url_path, 500),
                status_code, 1 if ok else 0, max(0, int(duration_ms)), attempt,
                _redact(error, 2000), correlation_id,
                _excerpt(request_excerpt), _excerpt(response_excerpt),
            ),
        )
        await conn.commit()


async def count_api_calls(
    *, service: str, operation: str, since: int, reached_server_only: bool = True,
) -> int:
    """Count calls in a window (used for client-side rate budgets)."""
    extra = " AND status_code IS NOT NULL" if reached_server_only else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT COUNT(*) FROM social_api_calls WHERE service=? AND operation=? AND created_at>=?{extra}",
            (service, operation, since),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def oldest_api_call_since(*, service: str, operation: str, since: int) -> int | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT MIN(created_at) FROM social_api_calls WHERE service=? AND operation=? "
            "AND created_at>=? AND status_code IS NOT NULL",
            (service, operation, since),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else None


async def list_api_calls(
    *,
    service: str = "",
    correlation_id: str = "",
    only_errors: bool = False,
    since: int | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if service:
        clauses.append("service=?")
        params.append(service)
    if correlation_id:
        clauses.append("correlation_id=?")
        params.append(correlation_id)
    if only_errors:
        clauses.append("ok=0")
    if since:
        clauses.append("created_at>=?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_api_calls {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


async def api_call_stats(since: int) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            """SELECT service, operation, COUNT(*) AS calls, SUM(ok) AS ok_calls,
                      SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failed_calls,
                      CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, MAX(created_at) AS last_at
               FROM social_api_calls WHERE created_at>=?
               GROUP BY service, operation ORDER BY service, operation""",
            (since,),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


# ---------------------------------------------------------------------------
# AI decisions
# ---------------------------------------------------------------------------

async def record_decision(
    *,
    store_id: str,
    decision_type: str,
    subject_id: str,
    model: str,
    valid: bool,
    used_fallback: bool,
    duration_ms: int,
    input_data: Any,
    output_data: Any,
    error: str = "",
) -> int:
    async with connect() as conn:
        cur = await conn.execute(
            """INSERT INTO social_decisions
               (created_at, store_id, decision_type, subject_id, model, valid, used_fallback,
                duration_ms, input_json, output_json, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now(), store_id, decision_type, subject_id, model, 1 if valid else 0,
                1 if used_fallback else 0, max(0, int(duration_ms)),
                _excerpt(input_data, 12000), _excerpt(output_data, 12000), _redact(error, 2000),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def list_decisions(
    *, store_id: str = "", subject_id: str = "", decision_type: str = "", limit: int = 50,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("store_id", store_id), ("subject_id", subject_id), ("decision_type", decision_type)):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_decisions {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ) as cur:
            rows = rows_to_dicts(await cur.fetchall())
    for row in rows:
        row["input"] = loads(row.pop("input_json", "{}"), row.get("input_json"))
        row["output"] = loads(row.pop("output_json", "{}"), row.get("output_json"))
    return rows


# ---------------------------------------------------------------------------
# Webhook deliveries (idempotent inbox)
# ---------------------------------------------------------------------------

async def record_webhook(
    *, source: str, topic: str, dedupe_key: str, payload: Any, store_id: str = "",
) -> tuple[int, bool]:
    """Store a delivery once. Returns (id, is_new)."""
    async with connect() as conn:
        cur = await conn.execute(
            """INSERT INTO social_webhook_events
               (store_id, source, topic, dedupe_key, payload_json, status, received_at)
               VALUES (?,?,?,?,?,'received',?)
               ON CONFLICT(source, dedupe_key) DO NOTHING""",
            (store_id, source, topic, dedupe_key, _excerpt(payload, 200000), now()),
        )
        await conn.commit()
        if cur.rowcount:
            return int(cur.lastrowid or 0), True
        async with conn.execute(
            "SELECT id FROM social_webhook_events WHERE source=? AND dedupe_key=?",
            (source, dedupe_key),
        ) as lookup:
            row = await lookup.fetchone()
        return (int(row["id"]) if row else 0), False


async def mark_webhook(event_id: int, *, status: str, error: str = "") -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE social_webhook_events SET status=?, error=?, processed_at=? WHERE id=?",
            (status, _redact(error, 2000), now(), event_id),
        )
        await conn.commit()


async def get_webhook(event_id: int) -> dict | None:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_webhook_events WHERE id=?", (event_id,)) as cur:
            return row_to_dict(await cur.fetchone(), {"payload": {}})


async def list_webhooks(*, source: str = "", limit: int = 50) -> list[dict]:
    where, params = ("WHERE source=?", (source,)) if source else ("", ())
    async with connect() as conn:
        async with conn.execute(
            f"SELECT id, store_id, source, topic, dedupe_key, status, error, received_at, processed_at "
            f"FROM social_webhook_events {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


# ---------------------------------------------------------------------------
# Engine state (heartbeats, sync cursors, counters)
# ---------------------------------------------------------------------------

async def set_state(key: str, value: Any) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_engine_state(key, value_json, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (key, dumps(value), now()),
        )
        await conn.commit()


async def get_state(key: str, default: Any = None) -> Any:
    async with connect() as conn:
        async with conn.execute("SELECT value_json FROM social_engine_state WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return loads(row["value_json"], default) if row else default


async def get_state_with_time(key: str) -> tuple[Any, int | None]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT value_json, updated_at FROM social_engine_state WHERE key=?", (key,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None, None
    return loads(row["value_json"], None), int(row["updated_at"])


async def list_state(prefix: str) -> dict[str, dict]:
    async with connect() as conn:
        async with conn.execute(
            "SELECT key, value_json, updated_at FROM social_engine_state WHERE key LIKE ?",
            (f"{prefix}%",),
        ) as cur:
            rows = await cur.fetchall()
    return {
        str(row["key"]): {"value": loads(row["value_json"], None), "updated_at": int(row["updated_at"])}
        for row in rows
    }


async def prune(*, activity_days: int = 90, api_call_days: int = 30, webhook_days: int = 30, decision_days: int = 90) -> dict[str, int]:
    cutoff = now()
    results: dict[str, int] = {}
    async with connect() as conn:
        for table, column, days in (
            ("social_activity", "created_at", activity_days),
            ("social_api_calls", "created_at", api_call_days),
            ("social_webhook_events", "received_at", webhook_days),
            ("social_decisions", "created_at", decision_days),
        ):
            cur = await conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff - days * 86400,))
            results[table] = cur.rowcount
        await conn.commit()
    return results
