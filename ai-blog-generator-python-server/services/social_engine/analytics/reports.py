"""Analytics roll-ups for the dashboard: per product, template, channel,
content type, campaign and hour — our differentiator over plain Postiz/Outfy."""
from __future__ import annotations

import time
from typing import Any

from db.social_engine import config_store
from db.social_engine import posts as post_store
from db.social_engine import products as product_store

from ..models import CONTENT_TYPE_LABELS

_DIMENSIONS = ("product", "template", "channel", "content_type", "campaign", "hour")


def _rate(row: dict) -> float:
    reach = float(row.get("impressions") or 0) or float(row.get("views") or 0) or float(row.get("reach") or 0)
    engaged = float(row.get("engagement") or 0) or sum(
        float(row.get(k) or 0) for k in ("likes", "comments", "shares", "saves", "clicks"))
    return round(engaged / reach * 100, 2) if reach else 0.0


async def performance_report(store_id: str, *, days: int = 30) -> dict[str, Any]:
    since = int(time.time()) - days * 86400 if days else None
    report: dict[str, Any] = {"days": days, "generated_at": int(time.time())}
    for dimension in _DIMENSIONS:
        rows = await post_store.metric_rollup(store_id, group_by=dimension, since=since, limit=50)
        for row in rows:
            row["engagement_rate"] = _rate(row)
        report[dimension] = rows

    # Human-readable labels.
    product_ids = [str(r["dimension"]) for r in report["product"]]
    products = {p["product_id"]: p for p in await product_store.get_products(store_id, product_ids)}
    for row in report["product"]:
        product = products.get(str(row["dimension"]))
        row["label"] = product["title"] if product else f"Product {row['dimension']}"
        row["handle"] = product.get("handle", "") if product else ""
    channels = {c["channel_id"]: c for c in await config_store.list_channels(store_id)}
    for row in report["channel"]:
        channel = channels.get(str(row["dimension"]))
        row["label"] = f"{channel['name']} ({channel['provider']})" if channel else str(row["dimension"])
    campaigns = {c["id"]: c for c in await config_store.list_campaigns(store_id)}
    for row in report["campaign"]:
        campaign = campaigns.get(str(row["dimension"]))
        row["label"] = campaign["name"] if campaign else ("Studio / manual" if not row["dimension"] else str(row["dimension"]))
    for row in report["content_type"]:
        row["label"] = CONTENT_TYPE_LABELS.get(str(row["dimension"]), str(row["dimension"]))
    for row in report["template"]:
        row["label"] = str(row["dimension"] or "—")
    for row in report["hour"]:
        row["label"] = f"{int(row['dimension']):02d}:00" if row["dimension"] is not None else "—"

    top = await post_store.top_posts(store_id, since=since, limit=10)
    for row in top:
        row["engagement_rate"] = _rate(row)
    report["top_posts"] = top
    totals: dict[str, float] = {}
    for row in report["channel"]:
        for key in ("impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks", "engagement"):
            totals[key] = totals.get(key, 0) + float(row.get(key) or 0)
    totals["engagement_rate"] = _rate(totals)
    report["totals"] = totals
    return report


async def learned_weights(store_id: str, *, days: int = 60, min_targets: int = 3) -> dict[str, Any]:
    """Engagement-rate weights per content type and best hours (feeds Gemma's planning)."""
    since = int(time.time()) - days * 86400
    by_type = await post_store.metric_rollup(store_id, group_by="content_type", since=since)
    by_hour = await post_store.metric_rollup(store_id, group_by="hour", since=since)
    weights = {str(r["dimension"]): _rate(r) for r in by_type if int(r.get("targets") or 0) >= min_targets}
    hours = sorted(
        (r for r in by_hour if int(r.get("targets") or 0) >= min_targets and r["dimension"] is not None),
        key=_rate, reverse=True,
    )
    return {"content_type_rates": weights, "best_hours": [int(r["dimension"]) for r in hours[:4]]}
