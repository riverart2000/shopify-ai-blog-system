"""Shared SQLite helpers for the Social Engine tables.

Both the API process and the scheduler/worker process write to the same SQLite
file, so every connection uses WAL mode and a generous busy timeout instead of
failing immediately with "database is locked".
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Mapping

import aiosqlite

from db.base import get_db_path

BUSY_TIMEOUT_SECONDS = 15.0


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with dict-like rows. Callers commit explicitly."""
    conn = await aiosqlite.connect(get_db_path(), timeout=BUSY_TIMEOUT_SECONDS)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
        yield conn
    finally:
        await conn.close()


def now() -> int:
    return int(time.time())


def new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex
    return f"{prefix}_{value}" if prefix else value


def dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, default=str, separators=(",", ":"))


def loads(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    if not isinstance(value, (str, bytes)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_to_dict(row: aiosqlite.Row | None, json_fields: Mapping[str, Any] | None = None) -> dict | None:
    """Convert a row to a dict and decode `<name>_json` columns into `<name>`.

    `json_fields` maps the decoded name to the default used when the column is
    empty or invalid, e.g. {"tags": [], "settings": {}}.
    """
    if row is None:
        return None
    item = dict(row)
    for field, default in (json_fields or {}).items():
        column = f"{field}_json"
        if column in item:
            fallback = default.copy() if isinstance(default, (list, dict)) else default
            item[field] = loads(item.pop(column), fallback)
    return item


def rows_to_dicts(rows: Iterable[aiosqlite.Row], json_fields: Mapping[str, Any] | None = None) -> list[dict]:
    return [row_to_dict(row, json_fields) for row in rows]  # type: ignore[misc]
