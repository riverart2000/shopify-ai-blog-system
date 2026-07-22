"""Persistent, central warning/error reporting for every backend process.

The logging handler is deliberately synchronous: logging can happen outside an
asyncio loop and in both the API and scheduler processes. Each write is short,
uses SQLite WAL mode, and must never be allowed to break the operation that was
being logged.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any

from db.base import get_db_path


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS system_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     INTEGER NOT NULL,
    level          TEXT NOT NULL,
    component      TEXT NOT NULL DEFAULT '',
    operation      TEXT NOT NULL DEFAULT '',
    store_id       TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL,
    details        TEXT NOT NULL DEFAULT '',
    resolved       INTEGER NOT NULL DEFAULT 0
)
"""

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:access[_-]?token|api[_-]?key|client[_-]?secret|password)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)([?&](?:token|key|signature|hmac)=)[^&#\s]+"),
)
_DATA_URI = re.compile(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]{80,}")


def _redact(value: object, limit: int = 8000) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    text = _DATA_URI.sub("[IMAGE DATA REDACTED]", text)
    if len(text) > limit:
        text = text[:limit] + "\n… [truncated]"
    return text


def _connect() -> sqlite3.Connection:
    path = Path(get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=3)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(_CREATE_TABLE)
    return connection


def record_event(
    *,
    level: str,
    message: object,
    component: str = "",
    operation: str = "",
    store_id: str = "",
    correlation_id: str = "",
    details: object = "",
) -> None:
    """Persist one event. Reporting failures are swallowed to prevent recursion."""
    try:
        now = int(time.time())
        with _connect() as connection:
            connection.execute(
                """INSERT INTO system_events
                   (created_at, level, component, operation, store_id,
                    correlation_id, message, details, resolved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    now,
                    str(level or "ERROR").upper(),
                    _redact(component, 200),
                    _redact(operation, 200),
                    _redact(store_id, 200),
                    _redact(correlation_id, 300),
                    _redact(message, 4000),
                    _redact(details, 8000),
                ),
            )
            # Keep the dashboard fast and bounded. Pruning is cheap and only
            # runs approximately once per 100 inserted events.
            row_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            if row_id % 100 == 0:
                connection.execute(
                    "DELETE FROM system_events WHERE created_at < ? OR id NOT IN "
                    "(SELECT id FROM system_events ORDER BY id DESC LIMIT 5000)",
                    (now - 90 * 86400,),
                )
    except Exception:
        # A diagnostics subsystem must never become a source of application
        # failures (and must not log its own error recursively).
        return


class SystemEventHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "skip_system_event", False):
                return
            details = ""
            if record.exc_info:
                details = "".join(traceback.format_exception(*record.exc_info))
            elif record.stack_info:
                details = record.stack_info
            component = record.name or "python"
            module_name = str(getattr(record, "module", "") or "")
            if module_name and not component.endswith(f".{module_name}"):
                component = f"{component}.{module_name}"
            operation = str(getattr(record, "operation", "") or record.funcName or "")
            store_id = str(getattr(record, "store_id", "") or "")
            correlation_id = str(getattr(record, "correlation_id", "") or "")
            record_event(
                level=record.levelname,
                message=record.getMessage(),
                component=component,
                operation=operation,
                store_id=store_id,
                correlation_id=correlation_id,
                details=details,
            )
        except Exception:
            return


def install_logging_handler() -> None:
    """Capture WARNING+ records and Python warnings once per process."""
    root = logging.getLogger()
    if any(getattr(handler, "is_system_event_handler", False) for handler in root.handlers):
        return
    handler = SystemEventHandler(level=logging.WARNING)
    handler.is_system_event_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    logging.captureWarnings(True)


def list_events(
    *,
    limit: int = 100,
    level: str = "",
    component: str = "",
    unresolved_only: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if level:
        clauses.append("level = ?")
        values.append(level.upper())
    if component:
        clauses.append("component LIKE ?")
        values.append(f"%{component}%")
    if unresolved_only:
        clauses.append("resolved = 0")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with _connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT * FROM system_events{where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                (*values, min(max(int(limit), 1), 500)),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        return []


def summary() -> dict[str, Any]:
    now = int(time.time())
    try:
        with _connect() as connection:
            connection.row_factory = sqlite3.Row
            counts = connection.execute(
                """SELECT
                     SUM(CASE WHEN level IN ('ERROR','CRITICAL') AND created_at >= ? THEN 1 ELSE 0 END) errors_24h,
                     SUM(CASE WHEN level = 'WARNING' AND created_at >= ? THEN 1 ELSE 0 END) warnings_24h,
                     SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) unresolved,
                     MAX(created_at) latest_at
                   FROM system_events""",
                (now - 86400, now - 86400),
            ).fetchone()
            return {
                "errors_24h": int(counts["errors_24h"] or 0),
                "warnings_24h": int(counts["warnings_24h"] or 0),
                "unresolved": int(counts["unresolved"] or 0),
                "latest_at": counts["latest_at"],
            }
    except Exception as exc:
        return {"errors_24h": 0, "warnings_24h": 0, "unresolved": 0, "latest_at": None, "error": _redact(exc)}


def set_resolved(event_id: int, resolved: bool) -> bool:
    try:
        with _connect() as connection:
            cursor = connection.execute(
                "UPDATE system_events SET resolved = ? WHERE id = ?",
                (1 if resolved else 0, int(event_id)),
            )
            return cursor.rowcount > 0
    except Exception:
        return False
