"""Durable job queue for the Social Engine worker.

Claiming uses a single UPDATE ... RETURNING statement, so two worker processes
can never run the same job. A claimed job holds a lease; if the worker dies the
lease expires and `recover_expired_leases` puts the job back in the queue (or
fails it when it has no attempts left).
"""
from __future__ import annotations

from typing import Any, Iterable

from .connection import connect, dumps, new_id, now, row_to_dict, rows_to_dicts

_JSON = {"payload": {}, "result": {}}

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


async def enqueue(
    job_type: str,
    payload: dict | None = None,
    *,
    store_id: str = "",
    run_at: int | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    dedupe_key: str = "",
    correlation_id: str = "",
) -> tuple[str, bool]:
    """Insert a job. Returns (job_id, created).

    When `dedupe_key` matches a queued/running job, nothing is inserted and the
    existing job id is returned with created=False.
    """
    job_id = new_id("job")
    timestamp = now()
    async with connect() as conn:
        if dedupe_key:
            async with conn.execute(
                "SELECT id FROM social_jobs WHERE dedupe_key=? AND status IN ('queued','running')",
                (dedupe_key,),
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                return str(existing["id"]), False
        try:
            await conn.execute(
                """INSERT INTO social_jobs
                   (id, store_id, type, payload_json, status, priority, run_at,
                    max_attempts, dedupe_key, correlation_id, created_at, updated_at)
                   VALUES (?,?,?,?,'queued',?,?,?,?,?,?,?)""",
                (
                    job_id, store_id, job_type, dumps(payload or {}), priority,
                    run_at if run_at is not None else timestamp, max(1, max_attempts),
                    dedupe_key, correlation_id, timestamp, timestamp,
                ),
            )
            await conn.commit()
        except Exception as exc:  # unique index race with another process
            if dedupe_key and "UNIQUE" in str(exc).upper():
                async with conn.execute(
                    "SELECT id FROM social_jobs WHERE dedupe_key=? AND status IN ('queued','running')",
                    (dedupe_key,),
                ) as cur:
                    existing = await cur.fetchone()
                if existing:
                    return str(existing["id"]), False
            raise
    return job_id, True


async def claim_next(
    *,
    owner: str,
    lease_seconds: int,
    allowed_types: Iterable[str] | None = None,
    excluded_types: Iterable[str] | None = None,
) -> dict | None:
    """Atomically claim the next due job, honouring type filters."""
    timestamp = now()
    clauses = ["status='queued'", "run_at<=?"]
    params: list[Any] = [timestamp]
    allowed = list(allowed_types or [])
    excluded = list(excluded_types or [])
    if allowed:
        clauses.append(f"type IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)
    if excluded:
        clauses.append(f"type NOT IN ({','.join('?' * len(excluded))})")
        params.extend(excluded)
    where = " AND ".join(clauses)
    sql = f"""
        UPDATE social_jobs
           SET status='running', lease_owner=?, lease_expires_at=?,
               attempts=attempts+1, started_at=?, updated_at=?
         WHERE id = (
               SELECT id FROM social_jobs WHERE {where}
               ORDER BY priority DESC, run_at ASC, created_at ASC LIMIT 1)
           AND status='queued'
        RETURNING *
    """
    async with connect() as conn:
        async with conn.execute(
            sql, (owner, timestamp + lease_seconds, timestamp, timestamp, *params),
        ) as cur:
            row = await cur.fetchone()
        await conn.commit()
    return row_to_dict(row, _JSON)


async def extend_lease(job_id: str, owner: str, lease_seconds: int) -> bool:
    async with connect() as conn:
        cur = await conn.execute(
            "UPDATE social_jobs SET lease_expires_at=?, updated_at=? "
            "WHERE id=? AND status='running' AND lease_owner=?",
            (now() + lease_seconds, now(), job_id, owner),
        )
        await conn.commit()
        return cur.rowcount > 0


async def complete(job_id: str, owner: str, result: dict | None = None) -> bool:
    timestamp = now()
    async with connect() as conn:
        cur = await conn.execute(
            """UPDATE social_jobs
               SET status='succeeded', result_json=?, finished_at=?, updated_at=?,
                   lease_owner='', lease_expires_at=NULL, last_error='', last_error_type=''
               WHERE id=? AND status='running' AND lease_owner=?""",
            (dumps(result or {}), timestamp, timestamp, job_id, owner),
        )
        await conn.commit()
        return cur.rowcount > 0


async def reschedule(
    job_id: str,
    owner: str,
    *,
    run_at: int,
    error_type: str = "",
    error: str = "",
    consume_attempt: bool = True,
) -> bool:
    """Return a running job to the queue for a later attempt."""
    attempt_fix = "" if consume_attempt else ", attempts=MAX(attempts-1, 0)"
    async with connect() as conn:
        cur = await conn.execute(
            f"""UPDATE social_jobs
                SET status='queued', run_at=?, last_error_type=?, last_error=?,
                    lease_owner='', lease_expires_at=NULL, updated_at=?{attempt_fix}
                WHERE id=? AND status='running' AND lease_owner=?""",
            (run_at, error_type, error[:4000], now(), job_id, owner),
        )
        await conn.commit()
        return cur.rowcount > 0


async def fail(job_id: str, owner: str, *, error_type: str, error: str) -> bool:
    timestamp = now()
    async with connect() as conn:
        cur = await conn.execute(
            """UPDATE social_jobs
               SET status='failed', last_error_type=?, last_error=?, finished_at=?,
                   updated_at=?, lease_owner='', lease_expires_at=NULL
               WHERE id=? AND status='running' AND lease_owner=?""",
            (error_type, error[:4000], timestamp, timestamp, job_id, owner),
        )
        await conn.commit()
        return cur.rowcount > 0


async def recover_expired_leases() -> dict[str, int]:
    """Requeue jobs whose worker died; fail those with no attempts left."""
    timestamp = now()
    async with connect() as conn:
        failed = await conn.execute(
            """UPDATE social_jobs
               SET status='failed', last_error_type='LeaseExpired',
                   last_error='Worker stopped or timed out while running this job; attempts exhausted.',
                   finished_at=?, updated_at=?, lease_owner='', lease_expires_at=NULL
               WHERE status='running' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < ? AND attempts >= max_attempts""",
            (timestamp, timestamp, timestamp),
        )
        requeued = await conn.execute(
            """UPDATE social_jobs
               SET status='queued', run_at=?, last_error_type='LeaseExpired',
                   last_error='Worker stopped or timed out while running this job; requeued.',
                   updated_at=?, lease_owner='', lease_expires_at=NULL
               WHERE status='running' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < ?""",
            (timestamp, timestamp, timestamp),
        )
        await conn.commit()
        return {"failed": failed.rowcount, "requeued": requeued.rowcount}


async def get(job_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_jobs WHERE id=?", (job_id,)) as cur:
            return row_to_dict(await cur.fetchone(), _JSON)


async def list_jobs(
    *,
    store_id: str = "",
    status: str = "",
    job_type: str = "",
    correlation_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if store_id:
        clauses.append("store_id IN (?, '')")
        params.append(store_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    if job_type:
        clauses.append("type=?")
        params.append(job_type)
    if correlation_id:
        clauses.append("correlation_id=?")
        params.append(correlation_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 500)), max(0, offset)),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _JSON)


async def counts_by_status(store_id: str = "") -> dict[str, int]:
    where, params = ("WHERE store_id IN (?, '')", (store_id,)) if store_id else ("", ())
    async with connect() as conn:
        async with conn.execute(
            f"SELECT status, COUNT(*) AS n FROM social_jobs {where} GROUP BY status", params,
        ) as cur:
            return {str(row["status"]): int(row["n"]) for row in await cur.fetchall()}


async def retry(job_id: str, *, run_at: int | None = None) -> bool:
    """Manually requeue a failed or cancelled job with a fresh attempt budget."""
    async with connect() as conn:
        cur = await conn.execute(
            """UPDATE social_jobs
               SET status='queued', run_at=?, attempts=0, finished_at=NULL, updated_at=?
               WHERE id=? AND status IN ('failed','cancelled')""",
            (run_at or now(), now(), job_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def cancel(job_id: str) -> bool:
    async with connect() as conn:
        cur = await conn.execute(
            """UPDATE social_jobs SET status='cancelled', finished_at=?, updated_at=?
               WHERE id=? AND status='queued'""",
            (now(), now(), job_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def cancel_for_correlation(correlation_id: str, job_types: Iterable[str] = ()) -> int:
    types = list(job_types)
    type_clause = f" AND type IN ({','.join('?' * len(types))})" if types else ""
    async with connect() as conn:
        cur = await conn.execute(
            f"""UPDATE social_jobs SET status='cancelled', finished_at=?, updated_at=?
                WHERE correlation_id=? AND status='queued'{type_clause}""",
            (now(), now(), correlation_id, *types),
        )
        await conn.commit()
        return cur.rowcount


async def count_running(job_types: Iterable[str]) -> int:
    types = list(job_types)
    if not types:
        return 0
    async with connect() as conn:
        async with conn.execute(
            f"SELECT COUNT(*) FROM social_jobs WHERE status='running' AND type IN ({','.join('?' * len(types))})",
            types,
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def prune_finished(older_than_seconds: int) -> int:
    async with connect() as conn:
        cur = await conn.execute(
            "DELETE FROM social_jobs WHERE status IN ('succeeded','cancelled') AND finished_at < ?",
            (now() - older_than_seconds,),
        )
        await conn.commit()
        return cur.rowcount
