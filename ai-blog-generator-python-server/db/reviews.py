"""Persistence for genuine customer product and store reviews."""
from __future__ import annotations

import csv
import io
import json
import time
import uuid

import aiosqlite

from .base import get_db_path


def _decode(row: aiosqlite.Row) -> dict:
    item = dict(row)
    try:
        item["moderation_flags"] = json.loads(item.get("moderation_flags") or "[]")
    except (json.JSONDecodeError, TypeError):
        item["moderation_flags"] = []
    item["verified_purchase"] = bool(item.get("verified_purchase"))
    return item


async def create_review(store_id: str, data: dict) -> dict:
    review_id = str(uuid.uuid4())
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO reviews
               (id, store_id, review_type, product_id, product_handle, product_title,
                rating, review_title, review_body, reviewer_name, reviewer_email,
                status, moderation_flags, photo_data, source, source_path, ip_hash,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id, store_id, data.get("review_type", "product"),
                data.get("product_id", ""), data.get("product_handle", ""),
                data.get("product_title", ""), int(data.get("rating", 0)),
                data.get("review_title", ""), data.get("review_body", ""),
                data.get("reviewer_name", ""), data.get("reviewer_email", ""),
                json.dumps(data.get("moderation_flags", []), ensure_ascii=False),
                data.get("photo_data", ""), data.get("source", "storefront"),
                data.get("source_path", ""), data.get("ip_hash", ""), now, now,
            ),
        )
        await conn.execute(
            "INSERT INTO review_audit (review_id, store_id, action, details, created_at) VALUES (?, ?, 'submitted', ?, ?)",
            (review_id, store_id, "Review submitted and held for moderation.", now),
        )
        await conn.commit()
    return await get_review(store_id, review_id) or {}


async def get_review(store_id: str, review_id: str) -> dict | None:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM reviews WHERE store_id=? AND id=?", (store_id, review_id)
        ) as cur:
            row = await cur.fetchone()
    return _decode(row) if row else None


async def rate_limit_count(store_id: str, ip_hash: str, since: int) -> int:
    if not ip_hash:
        return 0
    async with aiosqlite.connect(get_db_path()) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE store_id=? AND ip_hash=? AND created_at>=?",
            (store_id, ip_hash, int(since)),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def duplicate_count(
    store_id: str, reviewer_email: str, review_type: str, product_handle: str, since: int
) -> int:
    async with aiosqlite.connect(get_db_path()) as conn:
        async with conn.execute(
            """SELECT COUNT(*) FROM reviews
               WHERE store_id=? AND lower(reviewer_email)=lower(?) AND review_type=?
                 AND product_handle=? AND created_at>=? AND status!='spam'""",
            (store_id, reviewer_email, review_type, product_handle, int(since)),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def list_reviews(
    store_id: str,
    *,
    status: str = "",
    review_type: str = "",
    product_handle: str = "",
    rating: int = 0,
    limit: int = 100,
    offset: int = 0,
    public: bool = False,
    sort: str = "newest",
) -> tuple[list[dict], int]:
    clauses = ["store_id=?"]
    values: list[object] = [store_id]
    if public:
        clauses.append("status='published'")
    elif status:
        clauses.append("status=?")
        values.append(status)
    if review_type:
        clauses.append("review_type=?")
        values.append(review_type)
    if product_handle:
        clauses.append("product_handle=?")
        values.append(product_handle)
    if rating:
        clauses.append("rating=?")
        values.append(int(rating))
    where = " AND ".join(clauses)
    order = {
        "highest": "rating DESC, created_at DESC",
        "lowest": "rating ASC, created_at DESC",
        "oldest": "created_at ASC",
    }.get(sort, "created_at DESC")
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(f"SELECT COUNT(*) FROM reviews WHERE {where}", values) as cur:  # noqa: S608
            total_row = await cur.fetchone()
        async with conn.execute(
            f"SELECT * FROM reviews WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",  # noqa: S608
            (*values, min(max(int(limit), 1), 250), max(int(offset), 0)),
        ) as cur:
            rows = await cur.fetchall()
    return [_decode(row) for row in rows], int(total_row[0] or 0)


async def get_review_summary(store_id: str, product_handle: str = "", review_type: str = "") -> dict:
    clauses = ["store_id=?", "status='published'"]
    values: list[object] = [store_id]
    if product_handle:
        clauses.append("product_handle=?")
        values.append(product_handle)
    if review_type:
        clauses.append("review_type=?")
        values.append(review_type)
    where = " AND ".join(clauses)
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"SELECT COUNT(*) count, COALESCE(AVG(rating),0) average FROM reviews WHERE {where}",  # noqa: S608
            values,
        ) as cur:
            aggregate = await cur.fetchone()
        async with conn.execute(
            f"SELECT rating, COUNT(*) count FROM reviews WHERE {where} GROUP BY rating",  # noqa: S608
            values,
        ) as cur:
            distribution_rows = await cur.fetchall()
    distribution = {str(star): 0 for star in range(1, 6)}
    for row in distribution_rows:
        distribution[str(row["rating"])] = int(row["count"])
    return {
        "count": int(aggregate["count"] or 0),
        "average": round(float(aggregate["average"] or 0), 2),
        "distribution": distribution,
    }


async def get_admin_summary(store_id: str) -> dict:
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT
                 COUNT(*) total,
                 SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
                 SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) published,
                 SUM(CASE WHEN status IN ('rejected','spam','hidden') THEN 1 ELSE 0 END) not_published,
                 SUM(CASE WHEN status='published' AND merchant_reply='' THEN 1 ELSE 0 END) awaiting_reply,
                 COALESCE(AVG(CASE WHEN status='published' THEN rating END),0) average
               FROM reviews WHERE store_id=?""",
            (store_id,),
        ) as cur:
            row = await cur.fetchone()
    return {
        "total": int(row["total"] or 0), "pending": int(row["pending"] or 0),
        "published": int(row["published"] or 0),
        "not_published": int(row["not_published"] or 0),
        "awaiting_reply": int(row["awaiting_reply"] or 0),
        "average": round(float(row["average"] or 0), 2),
    }


async def moderate_review(
    store_id: str,
    review_id: str,
    *,
    status: str,
    merchant_reply: str | None = None,
    moderation_note: str = "",
    photo_url: str | None = None,
    clear_photo_data: bool = False,
) -> dict | None:
    now = int(time.time())
    assignments = ["status=?", "moderation_note=?", "updated_at=?"]
    values: list[object] = [status, moderation_note, now]
    if status == "published":
        assignments.append("published_at=COALESCE(published_at, ?)")
        values.append(now)
    if merchant_reply is not None:
        assignments.append("merchant_reply=?")
        values.append(merchant_reply)
    if photo_url is not None:
        assignments.append("photo_url=?")
        values.append(photo_url)
    if clear_photo_data:
        assignments.append("photo_data='' ")
    values.extend([store_id, review_id])
    async with aiosqlite.connect(get_db_path()) as conn:
        cursor = await conn.execute(
            f"UPDATE reviews SET {', '.join(assignments)} WHERE store_id=? AND id=?",  # noqa: S608
            values,
        )
        if cursor.rowcount:
            await conn.execute(
                "INSERT INTO review_audit (review_id, store_id, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (review_id, store_id, status, moderation_note, now),
            )
        await conn.commit()
    return await get_review(store_id, review_id) if cursor.rowcount else None


async def delete_review(store_id: str, review_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("DELETE FROM review_audit WHERE store_id=? AND review_id=?", (store_id, review_id))
        cursor = await conn.execute("DELETE FROM reviews WHERE store_id=? AND id=?", (store_id, review_id))
        await conn.commit()
    return cursor.rowcount > 0


async def export_reviews_csv(store_id: str) -> str:
    rows: list[dict] = []
    offset = 0
    while True:
        batch, total = await list_reviews(store_id, limit=250, offset=offset)
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= total:
            break
    output = io.StringIO()
    fields = [
        "id", "review_type", "product_handle", "product_title", "rating",
        "review_title", "review_body", "reviewer_name", "reviewer_email",
        "status", "verified_purchase", "merchant_reply", "photo_url",
        "source", "created_at", "published_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = dict(row)
        for key, value in safe.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                safe[key] = "'" + value
        writer.writerow(safe)
    return output.getvalue()
