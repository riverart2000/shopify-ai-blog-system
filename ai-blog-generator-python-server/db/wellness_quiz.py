"""Persistence and aggregate reporting for the storefront Wellness Quiz."""
from __future__ import annotations

import json
import time

import aiosqlite

from .base import get_db_path


def _decode_product(row: aiosqlite.Row) -> dict:
    item = dict(row)
    for source, target, fallback in (
        ("goal_scores_json", "goal_scores", {}),
        ("formats_json", "formats", []),
    ):
        try:
            item[target] = json.loads(item.pop(source) or json.dumps(fallback))
        except (json.JSONDecodeError, TypeError):
            item[target] = fallback
    item["available"] = bool(item.get("available"))
    return item


async def replace_wellness_quiz_products(store_id: str, products: list[dict]) -> None:
    now = int(time.time())
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute("DELETE FROM wellness_quiz_products WHERE store_id=?", (store_id,))
        for item in products:
            await conn.execute(
                """INSERT INTO wellness_quiz_products
                   (store_id, product_id, handle, title, product_url, landing_page_url,
                    guide_url, guide_title, image_url, price, currency, variant_id,
                    available, goal_scores_json, formats_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    store_id, str(item.get("product_id", "")), item.get("handle", ""),
                    item.get("title", ""), item.get("product_url", ""),
                    item.get("landing_page_url", ""), item.get("guide_url", ""),
                    item.get("guide_title", ""), item.get("image_url", ""),
                    float(item.get("price") or 0), item.get("currency", "GBP"),
                    str(item.get("variant_id", "")), 1 if item.get("available") else 0,
                    json.dumps(item.get("goal_scores", {}), ensure_ascii=False),
                    json.dumps(item.get("formats", []), ensure_ascii=False), now,
                ),
            )
        await conn.commit()


async def get_wellness_quiz_products(store_id: str, available_only: bool = True) -> list[dict]:
    where = "WHERE store_id=?" + (" AND available=1" if available_only else "")
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"SELECT * FROM wellness_quiz_products {where} ORDER BY title", (store_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [_decode_product(row) for row in rows]


async def record_wellness_quiz_event(store_id: str, event: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            """INSERT INTO wellness_quiz_events
               (store_id, session_id, event_type, goal, answers_json,
                recommendations_json, product_handle, source_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                store_id, str(event.get("session_id", ""))[:100],
                str(event.get("event_type", ""))[:60], str(event.get("goal", ""))[:60],
                json.dumps(event.get("answers", {}), ensure_ascii=False)[:10000],
                json.dumps(event.get("recommendations", []), ensure_ascii=False)[:10000],
                str(event.get("product_handle", ""))[:255],
                str(event.get("source_path", ""))[:1000], int(time.time()),
            ),
        )
        await conn.commit()


async def get_wellness_quiz_summary(store_id: str, period_days: int = 90) -> dict:
    cutoff = int(time.time()) - max(1, int(period_days)) * 86400
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT event_type, COUNT(*) AS count,
                      COUNT(DISTINCT session_id) AS sessions
               FROM wellness_quiz_events
               WHERE store_id=? AND created_at>=?
               GROUP BY event_type""",
            (store_id, cutoff),
        ) as cur:
            event_rows = await cur.fetchall()
        async with conn.execute(
            """SELECT goal, COUNT(*) AS completions
               FROM wellness_quiz_events
               WHERE store_id=? AND created_at>=? AND event_type='completed' AND goal!=''
               GROUP BY goal ORDER BY completions DESC""",
            (store_id, cutoff),
        ) as cur:
            goal_rows = await cur.fetchall()
        async with conn.execute(
            """SELECT product_handle, COUNT(*) AS clicks
               FROM wellness_quiz_events
               WHERE store_id=? AND created_at>=? AND event_type='recommendation_clicked'
                     AND product_handle!=''
               GROUP BY product_handle ORDER BY clicks DESC LIMIT 10""",
            (store_id, cutoff),
        ) as cur:
            product_rows = await cur.fetchall()
        async with conn.execute(
            "SELECT COUNT(*) AS products, MAX(updated_at) AS last_synced FROM wellness_quiz_products WHERE store_id=?",
            (store_id,),
        ) as cur:
            catalogue = await cur.fetchone()

    events = {row["event_type"]: int(row["count"]) for row in event_rows}
    unique = {row["event_type"]: int(row["sessions"]) for row in event_rows}
    starts = unique.get("started", 0)
    completions = unique.get("completed", 0)
    clicks = unique.get("recommendation_clicked", 0)
    return {
        "period_days": int(period_days),
        "starts": starts,
        "completions": completions,
        "completion_rate": round(completions / starts * 100, 2) if starts else 0.0,
        "recommendation_clickers": clicks,
        "click_through_rate": round(clicks / completions * 100, 2) if completions else 0.0,
        "events": events,
        "goals": [dict(row) for row in goal_rows],
        "top_products": [dict(row) for row in product_rows],
        "catalogue_products": int(catalogue["products"] or 0) if catalogue else 0,
        "last_synced": int(catalogue["last_synced"] or 0) if catalogue else 0,
    }
