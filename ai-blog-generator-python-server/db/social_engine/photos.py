"""Photo audits: which product photos carry text that must not be published.

Supplier photos often have "Slim Legs" or "Fat Burning" printed on them. Each
photo is checked once by the vision model and the verdict is kept by URL, so a
catalogue sync (which rewrites the product's image list) does not lose it.
"""
from __future__ import annotations

import json
from typing import Iterable

from .connection import connect, now, rows_to_dicts


async def get_audits(store_id: str, urls: Iterable[str]) -> dict[str, dict]:
    wanted = [u for u in dict.fromkeys(urls) if u]
    if not wanted:
        return {}
    marks = ",".join("?" for _ in wanted)
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_photo_audits WHERE store_id=? AND url IN ({marks})", (store_id, *wanted),
        ) as cur:
            rows = rows_to_dicts(await cur.fetchall(), {"claims": []})
    return {row["url"]: row for row in rows}


async def save_audit(store_id: str, *, url: str, product_id: str, verdict: str, claims: list[str], model: str) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_photo_audits(store_id, url, product_id, verdict, claims_json, model, audited_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(store_id, url) DO UPDATE SET product_id=excluded.product_id, verdict=excluded.verdict,
                 claims_json=excluded.claims_json, model=excluded.model, audited_at=excluded.audited_at""",
            (store_id, url, str(product_id), verdict, json.dumps(claims[:6]), model, now()),
        )
        await conn.commit()


async def product_summary(store_id: str) -> dict[str, dict]:
    """{product_id: {"audited": n, "claims": n}} for every product with audited photos."""
    async with connect() as conn:
        async with conn.execute(
            """SELECT product_id, COUNT(*) AS audited, SUM(CASE WHEN verdict='claim' THEN 1 ELSE 0 END) AS claims
               FROM social_photo_audits WHERE store_id=? GROUP BY product_id""", (store_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {str(r["product_id"]): {"audited": int(r["audited"]), "claims": int(r["claims"] or 0)} for r in rows}


async def blocked_product_ids(store_id: str) -> set[str]:
    """Products where every audited photo carries a claim (nothing clean to show)."""
    summary = await product_summary(store_id)
    return {pid for pid, s in summary.items() if s["audited"] and s["claims"] >= s["audited"]}


async def audited_product_ids(store_id: str) -> set[str]:
    return set((await product_summary(store_id)).keys())
