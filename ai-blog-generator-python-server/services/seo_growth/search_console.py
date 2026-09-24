"""Read-only Google Search Console adapter with no automatic retries."""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx


class SearchConsoleError(RuntimeError):
    """A final, user-actionable Search Console failure."""


def normalise_site_url(value: str) -> str:
    site = (value or "").strip()
    if not site:
        raise SearchConsoleError(
            "Search Console property is blank. Enter either sc-domain:bioluxelab.com "
            "or the exact URL-prefix property shown in Google Search Console."
        )
    if site.startswith("sc-domain:"):
        domain = site.split(":", 1)[1].strip().lower().rstrip("/")
        if not domain or "/" in domain:
            raise SearchConsoleError("The Search Console domain property is invalid.")
        return f"sc-domain:{domain}"
    if not site.startswith(("https://", "http://")):
        site = f"https://{site}"
    return site.rstrip("/") + "/"


async def _access_token(service_account_json: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise SearchConsoleError(
            "Google authentication support is not installed on the server."
        ) from exc

    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise SearchConsoleError("Search Console credentials are not valid JSON.") from exc
    if not info.get("client_email") or not info.get("private_key"):
        raise SearchConsoleError(
            "Search Console credentials must contain client_email and private_key."
        )

    def refresh() -> str:
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        )
        credentials.refresh(Request())
        return str(credentials.token)

    try:
        return await asyncio.to_thread(refresh)
    except Exception as exc:
        raise SearchConsoleError(f"Google authentication failed: {exc}") from exc


async def _query(
    site_url: str,
    token: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    url = (
        "https://searchconsole.googleapis.com/webmasters/v3/sites/"
        f"{quote(site_url, safe='')}/searchAnalytics/query"
    )
    payload = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": 25000,
        "dataState": "final",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise SearchConsoleError(f"Search Console request failed: {exc}") from exc
    if not response.is_success:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        hint = ""
        if response.status_code in (401, 403):
            hint = (
                " Add the service-account client_email as an Owner or Full user of this "
                "exact Search Console property."
            )
        raise SearchConsoleError(
            f"Search Console returned HTTP {response.status_code}: {str(detail)[:600]}.{hint}"
        )
    output: list[dict[str, Any]] = []
    for row in response.json().get("rows", []) or []:
        keys = row.get("keys", [])
        if len(keys) < 2:
            continue
        output.append({
            "query": str(keys[0]),
            "page": str(keys[1]),
            "clicks": float(row.get("clicks", 0) or 0),
            "impressions": float(row.get("impressions", 0) or 0),
            "ctr": float(row.get("ctr", 0) or 0),
            "position": float(row.get("position", 0) or 0),
        })
    return output


def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    clicks = sum(float(row.get("clicks", 0)) for row in rows)
    impressions = sum(float(row.get("impressions", 0)) for row in rows)
    weighted_position = sum(
        float(row.get("position", 0)) * float(row.get("impressions", 0))
        for row in rows
    )
    return {
        "clicks": round(clicks, 2),
        "impressions": round(impressions, 2),
        "ctr": round(clicks / impressions, 4) if impressions else 0,
        "position": round(weighted_position / impressions, 2) if impressions else 0,
        "rows": len(rows),
    }


def _top(rows: list[dict[str, Any]], dimension: str, limit: int = 20) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = {}
    for row in rows:
        key = str(row.get(dimension) or "").strip()
        if not key:
            continue
        item = grouped.setdefault(key, {
            "clicks": 0, "impressions": 0, "weighted_position": 0,
        })
        impressions = float(row.get("impressions", 0) or 0)
        item["clicks"] += float(row.get("clicks", 0) or 0)
        item["impressions"] += impressions
        item["weighted_position"] += float(row.get("position", 0) or 0) * impressions
    output = []
    for key, item in grouped.items():
        impressions = item["impressions"]
        clicks = item["clicks"]
        output.append({
            dimension: key,
            "clicks": round(clicks, 2),
            "impressions": round(impressions, 2),
            "ctr": round(clicks / impressions, 4) if impressions else 0,
            "position": round(item["weighted_position"] / impressions, 2) if impressions else 0,
        })
    return sorted(output, key=lambda item: (-item["clicks"], -item["impressions"], item[dimension]))[:limit]


async def collect_search_console(
    site_url: str,
    service_account_json: str,
    period_days: int,
) -> dict[str, Any]:
    """Collect equal current and previous periods for evidence-based comparisons."""
    site = normalise_site_url(site_url)
    token = await _access_token(service_account_json)
    days = min(max(int(period_days), 28), 365)
    current_end = date.today() - timedelta(days=2)
    current_start = current_end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    current, previous = await asyncio.gather(
        _query(site, token, current_start, current_end),
        _query(site, token, previous_start, previous_end),
    )
    return {
        "connected": True,
        "site_url": site,
        "period_days": days,
        "current_period": {"start": current_start.isoformat(), "end": current_end.isoformat()},
        "previous_period": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "current": current,
        "previous": previous,
        "totals": _totals(current),
        "previous_totals": _totals(previous),
        "top_queries": _top(current, "query"),
        "top_pages": _top(current, "page"),
    }
