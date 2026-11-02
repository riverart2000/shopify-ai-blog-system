"""Directory listings: one row per directory the store has been (or should be) listed on."""
from __future__ import annotations

from typing import Any

from .connection import connect, dumps, now, row_to_dict, rows_to_dicts

STATUSES = ("todo", "filled", "submitted", "verifying", "live", "broken", "skipped")
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
