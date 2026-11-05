"""SQLite store: jobs, steps, approvals, templates, site logins, email account, settings, worker state.

One file (WAL mode) shared by the worker and its API, which run in the same process.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

JOB_STATUSES = ("queued", "running", "waiting_approval", "done", "failed", "cancelled")
ACTIVE_STATUSES = ("queued", "running", "waiting_approval")

JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_jobs (
    id               INTEGER PRIMARY KEY,
    type             TEXT NOT NULL,
    title            TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    tools_allowed    TEXT NOT NULL DEFAULT '[]',
    priority         INTEGER NOT NULL DEFAULT 0,
    run_at           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'waiting_approval', 'done', 'failed', 'cancelled')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    idempotency_key  TEXT UNIQUE,
    checkpoint       TEXT,          -- saved message history, so a crashed job resumes instead of restarting
    result           TEXT,
    error            TEXT,
    created_by       TEXT NOT NULL DEFAULT 'cli',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    template_key     TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    parent_id        INTEGER,       -- a promotion's per-place jobs point at the promotion job
    template_values  TEXT           -- what the task was created with (JSON), so it can be cloned
);
"""

TARGETS_TABLE = """
CREATE TABLE IF NOT EXISTS promotion_targets (
    id            INTEGER PRIMARY KEY,
    promotion_id  INTEGER NOT NULL REFERENCES agent_jobs (id),
    kind          TEXT NOT NULL CHECK (kind IN ('listing', 'group', 'forum', 'page', 'own_social')),
    name          TEXT NOT NULL,
    url           TEXT NOT NULL DEFAULT '',
    why           TEXT NOT NULL DEFAULT '',
    account       TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'found' CHECK (source IN ('found', 'reused', 'own')),
    status        TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned', 'queued', 'working', 'posted', 'already_listed', 'needs_you',
                                    'skipped', 'failed')),
    job_id        INTEGER,
    scheduled_at  TEXT,
    result_url    TEXT,
    note          TEXT,
    asks          INTEGER NOT NULL DEFAULT 0,
    need_site     TEXT,
    need_url      TEXT,
    need_reason   TEXT,
    need_until    TEXT,
    data          TEXT,          -- extra JSON, e.g. the Social Autopilot channel ids
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

SCHEMA = (
    JOBS_TABLE
    + """
CREATE INDEX IF NOT EXISTS idx_agent_jobs_ready ON agent_jobs (status, run_at, priority);

"""
    + TARGETS_TABLE
    + """CREATE INDEX IF NOT EXISTS idx_promotion_targets ON promotion_targets (promotion_id);
CREATE INDEX IF NOT EXISTS idx_promotion_targets_job ON promotion_targets (job_id);

CREATE TABLE IF NOT EXISTS agent_steps (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES agent_jobs (id),
    n               INTEGER NOT NULL,
    tool            TEXT NOT NULL,
    args            TEXT,
    result_summary  TEXT,
    screenshot_path TEXT,
    tokens          INTEGER,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_steps_job ON agent_steps (job_id, n);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL REFERENCES agent_jobs (id),
    action      TEXT NOT NULL,
    preview     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status, created_at);

CREATE TABLE IF NOT EXISTS site_profiles (
    site             TEXT PRIMARY KEY,
    profile_dir      TEXT NOT NULL,
    last_login_ok_at TEXT,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS task_templates (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    task_type    TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    defaults     TEXT NOT NULL DEFAULT '{}',
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_account (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    username          TEXT NOT NULL DEFAULT '',
    from_name         TEXT NOT NULL DEFAULT '',
    imap_host         TEXT NOT NULL DEFAULT '',
    imap_port         INTEGER NOT NULL DEFAULT 993,
    smtp_host         TEXT NOT NULL DEFAULT '',
    smtp_port         INTEGER NOT NULL DEFAULT 587,
    last_test_at      TEXT,
    last_test_ok      INTEGER,
    last_test_message TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_types (
    key               TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    email             TEXT NOT NULL DEFAULT '',
    imap_host         TEXT NOT NULL DEFAULT '',
    imap_port         INTEGER NOT NULL DEFAULT 993,
    imap_username     TEXT NOT NULL DEFAULT '',
    facebook_page     TEXT NOT NULL DEFAULT '',
    linkedin_page     TEXT NOT NULL DEFAULT '',
    tag_handles       TEXT NOT NULL DEFAULT '',
    audience          TEXT NOT NULL DEFAULT '',
    enabled           INTEGER NOT NULL DEFAULT 1,
    last_test_at      TEXT,
    last_test_ok      INTEGER,
    last_test_message TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_state (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen_at      TEXT NOT NULL,
    current_job_id    INTEGER,
    current_job_title TEXT
);
"""
)

# Joe's event types, added once when the event_types table is first made (he can rename or remove them).
DEFAULT_EVENT_TYPES = (
    ("comedy", "Comedy"),
    ("bloomsbury-speakers", "Bloomsbury speakers"),
    ("biohacking", "Biohacking"),
    ("juggling", "Juggling"),
)

# Columns added to agent_jobs after the rebuild (ALTER TABLE is enough for these).
JOB_COLUMNS = {"parent_id": "INTEGER", "template_values": "TEXT"}

# Columns added to site_profiles after the first release.
SITE_PROFILE_COLUMNS = {
    "label": "TEXT NOT NULL DEFAULT ''",
    "login_url": "TEXT NOT NULL DEFAULT ''",
    "username": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "event_type": "TEXT NOT NULL DEFAULT ''",  # '' = shared by every event type (e.g. Facebook, LinkedIn)
}


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    title: str
    payload: dict[str, Any]
    tools_allowed: list[str]
    priority: int
    run_at: str
    status: str
    attempts: int
    idempotency_key: str | None
    checkpoint: str | None
    result: dict[str, Any] | None
    error: str | None
    created_by: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    template_key: str | None = None
    cancel_requested: bool = False
    parent_id: int | None = None
    template_values: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["payload"] = json.loads(data["payload"] or "{}")
        data["tools_allowed"] = json.loads(data["tools_allowed"] or "[]")
        data["result"] = json.loads(data["result"]) if data["result"] else None
        data["cancel_requested"] = bool(data.get("cancel_requested"))
        values = data.get("template_values")
        data["template_values"] = json.loads(values) if values else None
        return cls(**data)


class JobError(ValueError):
    """An action that doesn't fit the job's current state (shown to the user as-is)."""


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_jobs_table()
        self._migrate_targets_table()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            have_jobs = {r["name"] for r in db.execute("PRAGMA table_info(agent_jobs)")}
            if have_jobs:
                for column, ddl in JOB_COLUMNS.items():
                    if column not in have_jobs:
                        db.execute(f"ALTER TABLE agent_jobs ADD COLUMN {column} {ddl}")
            new_event_types = not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_types'"
            ).fetchone()
            db.executescript(SCHEMA)
            if new_event_types:
                stamp = iso(now())
                db.executemany(
                    "INSERT OR IGNORE INTO event_types (key, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    [(key, name, stamp, stamp) for key, name in DEFAULT_EVENT_TYPES],
                )
            have = {r["name"] for r in db.execute("PRAGMA table_info(site_profiles)")}
            for column, ddl in SITE_PROFILE_COLUMNS.items():
                if column not in have:
                    db.execute(f"ALTER TABLE site_profiles ADD COLUMN {column} {ddl}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:  # commits on success, rolls back on error
                yield db
        finally:
            db.close()

    def _migrate_jobs_table(self) -> None:
        """Rebuild agent_jobs from the first release (no 'cancelled' status, no template/cancel columns).

        SQLite can't change a CHECK constraint in place, so this follows its documented
        rebuild steps: copy into a new table, drop the old one, rename, with foreign keys off.
        """
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_jobs'").fetchone()
            if not row or "'cancelled'" in row["sql"]:
                return
            old_columns = [r["name"] for r in db.execute("PRAGMA table_info(agent_jobs)")]
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            db.execute(JOBS_TABLE.replace("agent_jobs", "agent_jobs_new", 1))
            new_columns = {r["name"] for r in db.execute("PRAGMA table_info(agent_jobs_new)")}
            columns = ", ".join(c for c in old_columns if c in new_columns)
            db.execute(f"INSERT INTO agent_jobs_new ({columns}) SELECT {columns} FROM agent_jobs")
            db.execute("DROP TABLE agent_jobs")
            db.execute("ALTER TABLE agent_jobs_new RENAME TO agent_jobs")
            problems = db.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                db.execute("ROLLBACK")
                raise RuntimeError(f"Job table upgrade stopped: foreign key problems {problems[:3]}")
            db.execute("COMMIT")
        finally:
            db.execute("PRAGMA foreign_keys=ON")
            db.close()

    def _migrate_targets_table(self) -> None:
        """Rebuild promotion_targets from before event types (no 'page' kind, no 'own' source)."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'promotion_targets'"
            ).fetchone()
            if not row or "'page'" in row["sql"]:
                return
            old_columns = [r["name"] for r in db.execute("PRAGMA table_info(promotion_targets)")]
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            db.execute(TARGETS_TABLE.replace("promotion_targets", "promotion_targets_new", 1))
            new_columns = {r["name"] for r in db.execute("PRAGMA table_info(promotion_targets_new)")}
            columns = ", ".join(c for c in old_columns if c in new_columns)
            db.execute(f"INSERT INTO promotion_targets_new ({columns}) SELECT {columns} FROM promotion_targets")
            db.execute("DROP TABLE promotion_targets")
            db.execute("ALTER TABLE promotion_targets_new RENAME TO promotion_targets")
            db.execute("COMMIT")
        finally:
            db.execute("PRAGMA foreign_keys=ON")
            db.close()

    # ── Jobs ────────────────────────────────────────────────────────────────

    def enqueue(
        self,
        type: str,
        title: str,
        payload: dict[str, Any],
        tools_allowed: list[str],
        *,
        priority: int = 0,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
        created_by: str = "cli",
        template_key: str | None = None,
        parent_id: int | None = None,
        template_values: dict[str, Any] | None = None,
    ) -> tuple[int, bool]:
        """Add a job. Returns (job_id, created). A repeated idempotency key returns the existing job."""
        stamp = iso(now())
        with self._connect() as db:
            if idempotency_key:
                row = db.execute("SELECT id FROM agent_jobs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if row:
                    return row["id"], False
            cur = db.execute(
                """INSERT INTO agent_jobs (type, title, payload, tools_allowed, priority, run_at,
                       idempotency_key, created_by, created_at, updated_at, template_key, parent_id,
                       template_values)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    type,
                    title,
                    json.dumps(payload),
                    json.dumps(tools_allowed),
                    priority,
                    iso(run_at or now()),
                    idempotency_key,
                    created_by,
                    stamp,
                    stamp,
                    template_key,
                    parent_id,
                    json.dumps(template_values) if template_values is not None else None,
                ),
            )
            return int(cur.lastrowid), True

    def claim_next(self) -> Job | None:
        """Atomically move the next ready job to 'running' and return it."""
        stamp = iso(now())
        with self._connect() as db:
            row = db.execute(
                """UPDATE agent_jobs
                      SET status = 'running', attempts = attempts + 1, started_at = ?, updated_at = ?
                    WHERE id = (SELECT id FROM agent_jobs
                                 WHERE status = 'queued' AND run_at <= ?
                                 ORDER BY priority DESC, run_at, id LIMIT 1)
                RETURNING *""",
                (stamp, stamp, stamp),
            ).fetchone()
        return Job.from_row(row) if row else None

    def requeue_interrupted(self) -> list[int]:
        """Jobs left 'running' by a crash or restart go back in the queue; they resume from their checkpoint.

        A job that was asked to cancel before the crash is cancelled instead.
        """
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                """UPDATE agent_jobs SET status = 'cancelled', finished_at = ?, updated_at = ?,
                       error = COALESCE(error, 'Cancelled from the Task Agent page.')
                    WHERE status = 'running' AND cancel_requested = 1""",
                (stamp, stamp),
            )
            rows = db.execute(
                "UPDATE agent_jobs SET status = 'queued', updated_at = ? WHERE status = 'running' RETURNING id",
                (stamp,),
            ).fetchall()
        return [r["id"] for r in rows]

    def save_checkpoint(self, job_id: int, messages_json: bytes) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET checkpoint = ?, updated_at = ? WHERE id = ?",
                (messages_json.decode(), iso(now()), job_id),
            )

    def finish(self, job_id: int, result: dict[str, Any]) -> None:
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                """UPDATE agent_jobs SET status = 'done', result = ?, error = NULL,
                       finished_at = ?, updated_at = ? WHERE id = ?""",
                (json.dumps(result), stamp, stamp, job_id),
            )

    def fail(self, job_id: int, error: str) -> None:
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET status = 'failed', error = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (error, stamp, stamp, job_id),
            )

    def retry_later(self, job_id: int, error: str, delay: timedelta) -> None:
        stamp = now()
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET status = 'queued', error = ?, run_at = ?, updated_at = ? WHERE id = ?",
                (error, iso(stamp + delay), iso(stamp), job_id),
            )

    def cancel(self, job_id: int) -> Job:
        """Cancel a job. A queued one stops now; a running one stops at its next step."""
        job = self.require(job_id)
        stamp = iso(now())
        with self._connect() as db:
            if job.status in ("queued", "waiting_approval"):
                db.execute(
                    """UPDATE agent_jobs SET status = 'cancelled', error = 'Cancelled from the Task Agent page.',
                           finished_at = ?, updated_at = ? WHERE id = ?""",
                    (stamp, stamp, job_id),
                )
            elif job.status == "running":
                db.execute("UPDATE agent_jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (stamp, job_id))
            else:
                raise JobError(f"Job {job_id} has already finished ({job.status}).")
        return self.require(job_id)

    def cancel_requested(self, job_id: int) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT cancel_requested FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def mark_cancelled(self, job_id: int, message: str = "Cancelled from the Task Agent page.") -> None:
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET status = 'cancelled', error = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (message, stamp, stamp, job_id),
            )

    def retry(self, job_id: int) -> Job:
        """Put a failed or cancelled job back in the queue to start over (its old steps stay in the log)."""
        job = self.require(job_id)
        if job.status not in ("failed", "cancelled"):
            raise JobError(f"Only failed or cancelled jobs can be retried (job {job_id} is {job.status}).")
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                """UPDATE agent_jobs SET status = 'queued', attempts = 0, checkpoint = NULL, result = NULL,
                       error = NULL, cancel_requested = 0, run_at = ?, started_at = NULL, finished_at = NULL,
                       updated_at = ? WHERE id = ?""",
                (stamp, stamp, job_id),
            )
        return self.require(job_id)

    def update_payload(self, job_id: int, changes: dict[str, Any]) -> None:
        """Change some fields of a job's payload (e.g. give a promotion's waiting posts the event picture)."""
        with self._connect() as db:
            row = db.execute("SELECT payload FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobError(f"There's no task {job_id}.")
            payload = json.loads(row["payload"] or "{}") | changes
            db.execute(
                "UPDATE agent_jobs SET payload = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), iso(now()), job_id),
            )

    def rename(self, job_id: int, title: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET title = ?, updated_at = ? WHERE id = ?", (title[:120], iso(now()), job_id)
            )

    def wait_for_joe(self, job_id: int, until: datetime, reason: str) -> None:
        """Park a job until Joe has done something on a site (or the wait runs out). It starts over then."""
        with self._connect() as db:
            db.execute(
                """UPDATE agent_jobs SET status = 'queued', attempts = 0, checkpoint = NULL, error = ?,
                       run_at = ?, updated_at = ? WHERE id = ?""",
                (f"Waiting for you: {reason}"[:500], iso(until), iso(now()), job_id),
            )

    def wake(self, job_id: int) -> None:
        """Run a queued job now instead of at its scheduled time."""
        stamp = iso(now())
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET run_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                (stamp, stamp, job_id),
            )

    def children(self, parent_id: int) -> list[Job]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM agent_jobs WHERE parent_id = ? ORDER BY id", (parent_id,)).fetchall()
        return [Job.from_row(r) for r in rows]

    def get(self, job_id: int) -> Job | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def require(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job is None:
            raise LookupError(f"No job {job_id}.")
        return job

    def list(self, status: str | None = None, limit: int = 50) -> list[Job]:
        jobs, _ = self.search([status] if status else None, limit=limit)
        return jobs

    def search(
        self,
        statuses: list[str] | None = None,
        q: str = "",
        *,
        limit: int = 20,
        offset: int = 0,
        include_children: bool = True,
    ) -> tuple[list[Job], int]:
        """Jobs newest first, filtered by status and title text. Returns (page, total matching)."""
        where, params = [], []
        if not include_children:
            where.append("parent_id IS NULL")
        if statuses:
            where.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if q.strip():
            where.append("title LIKE ? ESCAPE '\\'")
            params.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as db:
            total = db.execute(f"SELECT COUNT(*) AS n FROM agent_jobs {clause}", params).fetchone()["n"]
            rows = db.execute(
                f"SELECT * FROM agent_jobs {clause} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
        return [Job.from_row(r) for r in rows], int(total)

    def counts(self) -> dict[str, int]:
        result = dict.fromkeys(JOB_STATUSES, 0)
        with self._connect() as db:
            for row in db.execute("SELECT status, COUNT(*) AS n FROM agent_jobs GROUP BY status"):
                result[row["status"]] = int(row["n"])
        return result

    def step_counts(self, job_ids: list[int]) -> dict[int, int]:
        if not job_ids:
            return {}
        with self._connect() as db:
            rows = db.execute(
                f"SELECT job_id, COUNT(*) AS n FROM agent_steps WHERE job_id IN ({', '.join('?' for _ in job_ids)})"
                " GROUP BY job_id",
                job_ids,
            ).fetchall()
        return {int(r["job_id"]): int(r["n"]) for r in rows}

    # ── Steps (audit trail) ────────────────────────────────────────────────

    def next_step_number(self, job_id: int) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(n), 0) AS n FROM agent_steps WHERE job_id = ?", (job_id,)).fetchone()
        return int(row["n"]) + 1

    def add_step(
        self,
        job_id: int,
        n: int,
        tool: str,
        args: Any,
        result_summary: str,
        *,
        tokens: int | None = None,
        duration_ms: int | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO agent_steps (job_id, n, tool, args, result_summary, screenshot_path,
                       tokens, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    n,
                    tool,
                    json.dumps(args, default=str),
                    result_summary,
                    screenshot_path,
                    tokens,
                    duration_ms,
                    iso(now()),
                ),
            )

    def steps(self, job_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM agent_steps WHERE job_id = ? ORDER BY n", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Approvals ──────────────────────────────────────────────────────────

    def approvals(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        clause, params = ("WHERE a.status = ?", [status]) if status else ("", [])
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT a.*, j.title AS job_title FROM approvals a JOIN agent_jobs j ON j.id = a.job_id
                    {clause} ORDER BY a.id DESC LIMIT ?""",
                [*params, limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def approval_counts(self) -> dict[str, int]:
        result = {"pending": 0, "approved": 0, "rejected": 0}
        with self._connect() as db:
            for row in db.execute("SELECT status, COUNT(*) AS n FROM approvals GROUP BY status"):
                result[row["status"]] = int(row["n"])
        return result

    def decide_approval(self, approval_id: int, decision: str, by: str) -> dict[str, Any]:
        status = {"approve": "approved", "reject": "rejected"}.get(decision)
        if status is None:
            raise JobError("Decision must be 'approve' or 'reject'.")
        with self._connect() as db:
            row = db.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise LookupError(f"No approval {approval_id}.")
            if row["status"] != "pending":
                raise JobError(f"Approval {approval_id} was already {row['status']}.")
            db.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                (status, iso(now()), by, approval_id),
            )
            found = db.execute(
                "SELECT a.*, j.title AS job_title FROM approvals a JOIN agent_jobs j ON j.id = a.job_id WHERE a.id = ?",
                (approval_id,),
            ).fetchone()
        return dict(found)

    # ── Settings saved from the admin page (override .env) ──────────────────

    def setting_overrides(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT key, value FROM agent_settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def save_setting_overrides(self, values: dict[str, Any]) -> None:
        stamp = iso(now())
        with self._connect() as db:
            for key, value in values.items():
                db.execute(
                    """INSERT INTO agent_settings (key, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (key, json.dumps(value), stamp),
                )

    # ── Worker heartbeat ───────────────────────────────────────────────────

    def heartbeat(self, current_job_id: int | None, current_job_title: str | None) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO worker_state (id, last_seen_at, current_job_id, current_job_title) VALUES (1, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
                       current_job_id = excluded.current_job_id, current_job_title = excluded.current_job_title""",
                (iso(now()), current_job_id, current_job_title),
            )

    def worker_state(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM worker_state WHERE id = 1").fetchone()
        return dict(row) if row else None
