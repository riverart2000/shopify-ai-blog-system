"""services/publer_service.py — Thin Publer API client.

Uses workspace-scoped endpoints to list accounts and schedule/draft social posts.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("ai_blog_server")

_DEFAULT_BASE_URL = "https://app.publer.com/api/v1"
_DEFAULT_TIMEOUT_SECONDS = 45


class PublerError(Exception):
    """Raised for Publer API failures."""


def _base_url() -> str:
    return (os.environ.get("PUBLER_API_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (
        (os.environ.get("PUBLER_API_KEY") or "").strip()
        or (os.environ.get("PUBLER_API_TOKEN") or "").strip()
    )


def docs_url() -> str:
    return (os.environ.get("PUBLER_API_DOCS") or "https://publer.com/docs/api-reference/introduction").strip()


def is_configured() -> bool:
    return bool(_api_key())


def _headers(workspace_id: str = "") -> dict[str, str]:
    key = _api_key()
    if not key:
        raise PublerError(
            "PUBLER_API_KEY is not configured in the backend environment. "
            f"See docs: {docs_url()}"
        )

    headers = {
        "Authorization": f"Bearer-API {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if workspace_id.strip():
        headers["Publer-Workspace-Id"] = workspace_id.strip()
    return headers


def _extract_error_message(body: str, payload: Any) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        detail = payload.get("detail")
        if detail:
            return str(detail)
    return (body or "Unknown Publer API error").strip()


async def _request_json(
    *,
    method: str,
    path: str,
    workspace_id: str = "",
    payload: dict | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    headers = _headers(workspace_id)

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
        response = await client.request(method, url, headers=headers, json=payload)

    body = response.text or ""
    parsed: Any
    try:
        parsed = response.json()
    except Exception:
        parsed = None

    if response.status_code >= 400:
        message = _extract_error_message(body, parsed)
        raise PublerError(f"Publer API {response.status_code}: {message}")

    return parsed if parsed is not None else {}


def _normalize_workspaces(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("workspaces") or payload.get("data") or []
    else:
        items = []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("id") or "").strip()
        if not workspace_id:
            continue
        out.append(
            {
                "id": workspace_id,
                "name": str(item.get("name") or "Workspace").strip() or "Workspace",
                "role": str(item.get("role") or "").strip(),
                "picture": str(item.get("picture") or "").strip(),
            }
        )
    return out


def _normalize_accounts(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, dict):
        items = payload.get("accounts") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("id") or "").strip()
        if not account_id:
            continue
        out.append(
            {
                "id": account_id,
                "name": str(item.get("name") or "").strip(),
                "provider": str(item.get("provider") or "").strip().lower(),
                "type": str(item.get("type") or "").strip(),
                "picture": str(item.get("picture") or "").strip(),
                "status": str(item.get("status") or "active").strip().lower(),
            }
        )
    return out


def _resolve_job_id(payload: Any) -> str:
    if isinstance(payload, dict):
        direct = str(payload.get("job_id") or "").strip()
        if direct:
            return direct
        data = payload.get("data")
        if isinstance(data, dict):
            nested = str(data.get("job_id") or "").strip()
            if nested:
                return nested
    return ""


async def list_workspaces() -> list[dict[str, str]]:
    payload = await _request_json(method="GET", path="/workspaces")
    return _normalize_workspaces(payload)


async def list_accounts(workspace_id: str) -> list[dict[str, str]]:
    workspace_id = workspace_id.strip()
    if not workspace_id:
        raise PublerError("workspace_id is required to list accounts.")
    payload = await _request_json(method="GET", path="/accounts", workspace_id=workspace_id)
    return _normalize_accounts(payload)


async def create_text_post(
    *,
    workspace_id: str,
    account_ids: list[str],
    provider_texts: dict[str, str],
    mode: str,
    scheduled_at: str = "",
) -> dict[str, Any]:
    workspace_id = workspace_id.strip()
    if not workspace_id:
        raise PublerError("workspace_id is required.")

    normalized_accounts = [str(account_id).strip() for account_id in account_ids if str(account_id).strip()]
    if not normalized_accounts:
        raise PublerError("At least one Publer account must be selected.")

    networks: dict[str, dict[str, str]] = {}
    for provider, text in (provider_texts or {}).items():
        p = str(provider or "").strip().lower()
        t = str(text or "").strip()
        if not p or not t:
            continue
        networks[p] = {"type": "status", "text": t}

    if not networks:
        raise PublerError("No provider post content was provided.")

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"draft", "scheduled", "publish_now"}:
        raise PublerError("mode must be one of: draft, scheduled, publish_now")

    if normalized_mode == "publish_now":
        endpoint = "/posts/schedule/publish"
        state = "scheduled"
    else:
        endpoint = "/posts/schedule"
        state = "draft" if normalized_mode == "draft" else "scheduled"

    accounts_payload: list[dict[str, str]] = []
    for account_id in normalized_accounts:
        account_entry: dict[str, str] = {"id": account_id}
        if state == "scheduled":
            if not scheduled_at.strip():
                raise PublerError("scheduled_at is required when mode is scheduled.")
            account_entry["scheduled_at"] = scheduled_at.strip()
        accounts_payload.append(account_entry)

    payload = {
        "bulk": {
            "state": state,
            "posts": [
                {
                    "networks": networks,
                    "accounts": accounts_payload,
                }
            ],
        }
    }

    raw = await _request_json(
        method="POST",
        path=endpoint,
        workspace_id=workspace_id,
        payload=payload,
    )
    job_id = _resolve_job_id(raw)
    if not job_id:
        logger.warning("Publer create_text_post returned no job_id: %s", raw)

    return {
        "job_id": job_id,
        "payload": raw,
        "request": payload,
    }


async def get_job_status(*, workspace_id: str, job_id: str) -> dict[str, Any]:
    workspace_id = workspace_id.strip()
    job_id = job_id.strip()
    if not workspace_id:
        raise PublerError("workspace_id is required.")
    if not job_id:
        raise PublerError("job_id is required.")

    raw = await _request_json(
        method="GET",
        path=f"/job_status/{job_id}",
        workspace_id=workspace_id,
    )

    if not isinstance(raw, dict):
        return {"status": "unknown", "payload": {}, "raw": raw}

    status = str(raw.get("status") or "").strip().lower()
    data_obj = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    result_obj = data_obj.get("result") if isinstance(data_obj.get("result"), dict) else {}

    if not status:
        status = str(data_obj.get("status") or result_obj.get("status") or "").strip().lower()

    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = result_obj.get("payload") if isinstance(result_obj.get("payload"), dict) else {}

    failures = payload.get("failures") if isinstance(payload, dict) else None
    if failures is None:
        failures = []

    return {
        "status": status or "unknown",
        "payload": payload,
        "failures": failures,
        "raw": raw,
    }
