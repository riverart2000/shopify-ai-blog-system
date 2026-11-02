"""Founder videos: owner recordings that become a full YouTube post plus Postiz clips."""
from __future__ import annotations

import json
from typing import Any, Iterable

from .connection import connect, new_id, now

_JSON = {"product_ids": "product_ids_json", "clips": "clips_json", "clip_post_ids": "clip_post_ids_json"}
_COLUMNS = (
    "id", "store_id", "source", "title", "note", "original_name", "original_path", "sha256", "bytes",
    "duration_ms", "width", "height", "product_ids_json", "youtube_url", "full_post_id", "clipping_id",
    "clips_json", "clip_post_ids_json", "status", "error", "next_check_at", "created_at", "updated_at",
)


def _row(row: Any) -> dict:
    data = dict(row)
    for key, column in _JSON.items():
        try:
            data[key] = json.loads(data.pop(column) or ("[]"))
        except ValueError:
            data[key] = []
    return data


def _encode(fields: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _JSON:
            out[_JSON[key]] = json.dumps(value or [])
        else:
            out[key] = value
    return out


async def create_video(store_id: str, video: dict) -> str:
    video_id = video.get("id") or new_id("fv")
    data = _encode({**video, "id": video_id, "store_id": store_id})
    data.setdefault("created_at", now())
    data.setdefault("updated_at", now())
    columns = [c for c in _COLUMNS if c in data]
    async with connect() as conn:
        await conn.execute(
            f"INSERT INTO social_founder_videos ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [data[c] for c in columns],
        )
        await conn.commit()
    return video_id


async def get_video(store_id: str, video_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute("SELECT * FROM social_founder_videos WHERE store_id=? AND id=?", (store_id, video_id)) as cur:
            row = await cur.fetchone()
    return _row(row) if row else None


async def find_by_sha(store_id: str, sha256: str) -> dict | None:
    if not sha256:
        return None
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_founder_videos WHERE store_id=? AND sha256=? ORDER BY created_at DESC LIMIT 1",
            (store_id, sha256),
        ) as cur:
            row = await cur.fetchone()
    return _row(row) if row else None


async def find_by_youtube_url(store_id: str, url: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_founder_videos WHERE store_id=? AND youtube_url=? AND status!='failed' "
            "ORDER BY created_at DESC LIMIT 1", (store_id, url),
        ) as cur:
            row = await cur.fetchone()
    return _row(row) if row else None


async def list_videos(store_id: str, *, statuses: Iterable[str] = (), limit: int = 100) -> list[dict]:
    clauses, params = ["store_id=?"], [store_id]
    status_list = [s for s in statuses if s]
    if status_list:
        clauses.append(f"status IN ({', '.join('?' for _ in status_list)})")
        params += status_list
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_founder_videos WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
            [*params, int(limit)],
        ) as cur:
            rows = await cur.fetchall()
    return [_row(r) for r in rows]


async def update_video(store_id: str, video_id: str, **fields: Any) -> bool:
    data = _encode(fields)
    data["updated_at"] = now()
    columns = [c for c in data if c in _COLUMNS and c not in ("id", "store_id", "created_at")]
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_founder_videos SET {', '.join(f'{c}=?' for c in columns)} WHERE store_id=? AND id=?",
            [data[c] for c in columns] + [store_id, video_id],
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_video(store_id: str, video_id: str) -> bool:
    async with connect() as conn:
        cur = await conn.execute("DELETE FROM social_founder_videos WHERE store_id=? AND id=?", (store_id, video_id))
        await conn.commit()
        return cur.rowcount > 0
