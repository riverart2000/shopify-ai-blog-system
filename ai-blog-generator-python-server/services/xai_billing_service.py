"""Read-only xAI billing preflight for paid generation jobs.

xAI billing is exposed by the Management API, which deliberately uses a
different credential from the inference API.  This module never retries.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests


def _money(cents: int | None) -> str:
    return "Unavailable" if cents is None else f"${cents / 100:,.2f}"


def _result(
    *,
    status: str,
    can_start: bool,
    message: str,
    available_cents: int | None = None,
    raw_balance_cents: int | None = None,
    minimum_cents: int | None = None,
) -> dict[str, Any]:
    if minimum_cents is None:
        minimum_cents = max(0, int(os.environ.get("XAI_MIN_CREDIT_CENTS", "100")))
    return {
        "provider": "xAI",
        "status": status,
        "can_start": can_start,
        "available_cents": available_cents,
        "available_display": _money(available_cents),
        "minimum_cents": minimum_cents,
        "minimum_display": _money(minimum_cents),
        "raw_balance_cents": raw_balance_cents,
        "message": message,
        "checked_at": int(time.time()),
        "retry_attempted": False,
    }


def check_xai_credit() -> dict[str, Any]:
    """Return prepaid credit state. No retry and no inference fallback."""
    try:
        minimum_cents = max(
            0, int(os.environ.get("XAI_MIN_CREDIT_CENTS", "100"))
        )
    except ValueError:
        return _result(
            status="configuration_error",
            can_start=False,
            minimum_cents=100,
            message=(
                "XAI_MIN_CREDIT_CENTS must be a whole number of cents. "
                "No generation request was sent."
            ),
        )
    management_key = (
        os.environ.get("XAI_MANAGEMENT_API_KEY")
        or os.environ.get("GROK_BILLING_API_KEY")
        or ""
    ).strip()
    team_id = (
        os.environ.get("XAI_TEAM_ID")
        or os.environ.get("GROK_TEAM_ID")
        or ""
    ).strip()
    if not management_key or not team_id:
        missing = []
        if not management_key:
            missing.append("GROK_BILLING_API_KEY (or XAI_MANAGEMENT_API_KEY)")
        if not team_id:
            missing.append("XAI_TEAM_ID (or GROK_TEAM_ID)")
        return _result(
            status="configuration_error",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                "Credit check is not configured. Missing "
                + " and ".join(missing)
                + ". xAI requires a Management API key (not the normal Grok API key) "
                  "and the xAI team ID. No generation request was sent."
            ),
        )

    base_url = (
        os.environ.get("XAI_MANAGEMENT_BASE_URL") or "https://management-api.x.ai"
    ).rstrip("/")
    try:
        timeout = max(
            1, int(os.environ.get("XAI_MANAGEMENT_TIMEOUT_SECONDS", "15"))
        )
    except ValueError:
        return _result(
            status="configuration_error",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                "XAI_MANAGEMENT_TIMEOUT_SECONDS must be a whole number of seconds. "
                "No generation request was sent."
            ),
        )
    endpoint = f"{base_url}/v1/billing/teams/{team_id}/prepaid/balance"
    try:
        response = requests.get(
            endpoint,
            headers={
                "Authorization": f"Bearer {management_key}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except requests.Timeout as exc:
        return _result(
            status="check_timeout",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                f"xAI credit check timed out after {timeout} seconds: {exc}. "
                "No generation request was sent and no retry was attempted."
            ),
        )
    except requests.RequestException as exc:
        readable_error = str(exc).replace(
            "Max retries exceeded with url", "Connection attempt failed for URL"
        )
        return _result(
            status="check_failed",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                f"xAI credit check failed ({type(exc).__name__}): {readable_error}. "
                "No generation request was sent and no retry was attempted."
            ),
        )

    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("error") or body.get("detail") or body
        except Exception:
            detail = response.text
        return _result(
            status="check_failed",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                f"xAI Management API returned HTTP {response.status_code}: "
                f"{str(detail)[:800]}. No generation request was sent and no "
                "retry was attempted."
            ),
        )

    try:
        payload = response.json()
        raw_value = int(payload["total"]["val"])
        # xAI's Management API represents available prepaid funds as a
        # negative account balance (for example -1000 means $10.00 credit).
        available_cents = max(0, -raw_value)
    except (KeyError, TypeError, ValueError) as exc:
        return _result(
            status="invalid_response",
            can_start=False,
            minimum_cents=minimum_cents,
            message=(
                f"xAI returned an unreadable credit response ({type(exc).__name__}: "
                f"{exc}). No generation request was sent and no retry was attempted."
            ),
        )

    if available_cents < minimum_cents:
        return _result(
            status="insufficient_credit",
            can_start=False,
            available_cents=available_cents,
            raw_balance_cents=raw_value,
            minimum_cents=minimum_cents,
            message=(
                f"xAI credit is {_money(available_cents)}, below the required "
                f"minimum of {_money(minimum_cents)}. No generation request was sent."
            ),
        )
    return _result(
        status="available",
        can_start=True,
        available_cents=available_cents,
        raw_balance_cents=raw_value,
        minimum_cents=minimum_cents,
        message=(
            f"xAI credit available: {_money(available_cents)}. "
            f"Required minimum: {_money(minimum_cents)}."
        ),
    )
