"""Read-only GA4 Data API connector for the intelligence dashboard."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


class GA4ConfigurationError(RuntimeError):
    pass


def _normalise_property_id(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("properties/"):
        value = value.split("/", 1)[1]
    if not value or not value.isdigit():
        raise GA4ConfigurationError("GA4 property ID must be numeric")
    return value


async def _access_token(service_account_json: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise GA4ConfigurationError("Google Analytics support is not installed") from exc

    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise GA4ConfigurationError("Service account credentials are not valid JSON") from exc

    def refresh() -> str:
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
        credentials.refresh(Request())
        return str(credentials.token)

    try:
        return await asyncio.to_thread(refresh)
    except Exception as exc:
        raise GA4ConfigurationError(f"Google authentication failed: {exc}") from exc


def _number(value: str) -> int | float:
    try:
        number = float(value)
        return int(number) if number.is_integer() else round(number, 4)
    except (TypeError, ValueError):
        return 0


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = [item.get("name", "") for item in body.get("dimensionHeaders", [])]
    metrics = [item.get("name", "") for item in body.get("metricHeaders", [])]
    output = []
    for row in body.get("rows", []) or []:
        item: dict[str, Any] = {}
        for name, value in zip(dimensions, row.get("dimensionValues", [])):
            item[name] = value.get("value", "")
        for name, value in zip(metrics, row.get("metricValues", [])):
            item[name] = _number(value.get("value", "0"))
        output.append(item)
    return output


async def _run_report(property_id: str, token: str, payload: dict) -> dict:
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    if not response.is_success:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        raise GA4ConfigurationError(f"GA4 returned {response.status_code}: {detail[:400]}")
    return response.json()


async def collect_ga4(
    property_id: str,
    service_account_json: str,
    period_days: int,
) -> dict:
    """Collect aggregate behaviour only; no user identifiers or PII are requested."""
    property_id = _normalise_property_id(property_id)
    token = await _access_token(service_account_json)
    date_ranges = [{"startDate": f"{period_days}daysAgo", "endDate": "yesterday"}]

    overview_payload = {
        "dateRanges": date_ranges,
        "metrics": [
            {"name": "sessions"}, {"name": "activeUsers"},
            {"name": "engagedSessions"}, {"name": "keyEvents"},
            {"name": "purchaseRevenue"},
        ],
    }
    channel_payload = {
        "dateRanges": date_ranges,
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 20,
    }
    device_payload = {
        "dateRanges": date_ranges,
        "dimensions": [{"name": "deviceCategory"}],
        "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 10,
    }
    landing_payload = {
        "dateRanges": date_ranges,
        "dimensions": [{"name": "landingPagePlusQueryString"}],
        "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 25,
    }

    overview_body, channel_body, device_body, landing_body = await asyncio.gather(
        _run_report(property_id, token, overview_payload),
        _run_report(property_id, token, channel_payload),
        _run_report(property_id, token, device_payload),
        _run_report(property_id, token, landing_payload),
    )
    overview_rows = _rows(overview_body)
    return {
        "connected": True,
        "property_id": property_id,
        "overview": overview_rows[0] if overview_rows else {},
        "channels": _rows(channel_body),
        "devices": _rows(device_body),
        "landing_pages": _rows(landing_body),
    }
