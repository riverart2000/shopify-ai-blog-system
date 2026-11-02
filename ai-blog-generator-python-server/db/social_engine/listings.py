"""Directory listings: one row per directory the store has been (or should be) listed on."""
from __future__ import annotations

from typing import Any

from .connection import connect, dumps, now, row_to_dict, rows_to_dicts

STATUSES = ("todo", "working", "filled", "submitted", "verifying", "needs_owner", "live", "broken", "skipped")
_JSON = {"fill": {}, "check": {}}
_TIMESTAMPS = {"filled": "filled_at", "submitted": "submitted_at", "live": "live_at"}


async def list_listings(store_id: str) -> dict[str, dict]:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_listings WHERE store_id=?", (store_id,)) as cur:
            rows = rows_to_dicts(await cur.fetchall(), _JSON)
    return {row["directory_id"]: row for row in rows}


async def get_listing(store_id: str, directory_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_listings WHERE store_id=? AND directory_id=?", (store_id, directory_id),
        ) as cur:
            return row_to_dict(await cur.fetchone(), _JSON)


async def save_listing(store_id: str, directory_id: str, **fields: Any) -> dict:
    """Upsert; `fill`/`check` dicts are stored as JSON, a status change stamps its timestamp."""
    data: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _JSON:
            data[f"{key}_json"] = dumps(value or {})
        else:
            data[key] = value
    status = fields.get("status")
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Unknown listing status '{status}'")
        stamp = _TIMESTAMPS.get(status)
        if stamp:
            data.setdefault(stamp, now())
    data["updated_at"] = now()
    columns = ["store_id", "directory_id", *data.keys()]
    updates = ", ".join(f"{c}=excluded.{c}" for c in data)
    async with connect() as conn:
        await conn.execute(
            f"INSERT INTO social_listings ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(store_id, directory_id) DO UPDATE SET {updates}",
            [store_id, directory_id, *data.values()],
        )
        await conn.commit()
    return (await get_listing(store_id, directory_id)) or {}


async def due_for_check(store_id: str, *, older_than: int) -> list[dict]:
    """Live listings with a URL whose last check is older than `older_than` (epoch)."""
    async with connect() as conn:
        async with conn.execute(
            """SELECT * FROM social_listings WHERE store_id=? AND status IN ('live','broken') AND listing_url!=''
               AND (checked_at IS NULL OR checked_at < ?) ORDER BY checked_at""", (store_id, older_than),
        ) as cur:
            return rows_to_dicts(await cur.fetchall(), _JSON)


# ---------------------------------------------------------------------- the worker's step log

async def add_step(store_id: str, directory_id: str, run_id: str, step: int, *, action: str, url: str = "", ref: str = "",
                   label: str = "", value: str = "", note: str = "", result: str = "ok", screenshot: str = "") -> int:
    async with connect() as conn:
        cur = await conn.execute(
            """INSERT INTO social_listing_steps (store_id, directory_id, run_id, step, at, url, action, ref, label, value, note, result, screenshot)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (store_id, directory_id, run_id, step, now(), url[:500], action, ref[:64], label[:200], value[:500], note[:400],
             result[:400], screenshot[:500]),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def latest_run_id(store_id: str, directory_id: str) -> str:
    async with connect() as conn:
        async with conn.execute(
            "SELECT run_id FROM social_listing_steps WHERE store_id=? AND directory_id=? ORDER BY id DESC LIMIT 1",
            (store_id, directory_id),
        ) as cur:
            row = await cur.fetchone()
    return str(row[0]) if row else ""


async def list_steps(store_id: str, directory_id: str, *, run_id: str = "", limit: int = 120) -> list[dict]:
    """Steps of one run (the latest when `run_id` is empty), oldest first."""
    run_id = run_id or await latest_run_id(store_id, directory_id)
    if not run_id:
        return []
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_listing_steps WHERE store_id=? AND directory_id=? AND run_id=? ORDER BY id LIMIT ?",
            (store_id, directory_id, run_id, limit),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


async def list_runs(store_id: str, directory_id: str, *, limit: int = 10) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            """SELECT run_id, MIN(at) AS started_at, MAX(at) AS ended_at, COUNT(*) AS steps,
                      (SELECT action FROM social_listing_steps s2 WHERE s2.run_id=s.run_id ORDER BY id DESC LIMIT 1) AS outcome
               FROM social_listing_steps s WHERE store_id=? AND directory_id=? GROUP BY run_id ORDER BY started_at DESC LIMIT ?""",
            (store_id, directory_id, limit),
        ) as cur:
            return rows_to_dicts(await cur.fetchall())


# ---------------------------------------------------------------------- accounts the worker created

async def get_account(store_id: str, directory_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_listing_accounts WHERE store_id=? AND directory_id=?", (store_id, directory_id),
        ) as cur:
            return row_to_dict(await cur.fetchone())


async def save_account(store_id: str, directory_id: str, **fields: Any) -> dict:
    data = {k: v for k, v in fields.items() if k in {"login_email", "username", "password_enc", "login_url", "note"}}
    data["updated_at"] = now()
    columns = ["store_id", "directory_id", *data.keys()]
    updates = ", ".join(f"{c}=excluded.{c}" for c in data)
    async with connect() as conn:
        await conn.execute(
            f"INSERT INTO social_listing_accounts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(store_id, directory_id) DO UPDATE SET {updates}",
            [store_id, directory_id, *data.values()],
        )
        await conn.commit()
    return (await get_account(store_id, directory_id)) or {}


async def list_accounts(store_id: str) -> dict[str, dict]:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_listing_accounts WHERE store_id=?", (store_id,)) as cur:
            rows = rows_to_dicts(await cur.fetchall())
    return {row["directory_id"]: row for row in rows}
