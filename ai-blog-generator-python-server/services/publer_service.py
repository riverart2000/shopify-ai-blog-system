"""services/publer_service.py — Thin Publer API client.

Uses workspace-scoped endpoints to list accounts and schedule/draft social posts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("ai_blog_server")

_DEFAULT_BASE_URL = "https://app.publer.com/api/v1"
_DEFAULT_TIMEOUT_SECONDS = 45
_MEDIA_UPLOAD_MAX_WAIT_SECONDS = 120
_MEDIA_UPLOAD_POLL_INTERVAL_SECONDS = 2
_TEXT_IMAGE_PROVIDERS = {"instagram", "facebook", "twitter", "linkedin", "pinterest"}
_VIDEO_ONLY_PROVIDERS = {"tiktok", "youtube"}
_PROVIDER_ALIASES = {
    "x": "twitter",
    "twitter": "twitter",
    "instagram": "instagram",
    "facebook": "facebook",
    "linkedin": "linkedin",
    "pinterest": "pinterest",
}


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


def _normalise_status_payload(raw: Any) -> tuple[str, dict[str, Any], list[Any]]:
    if not isinstance(raw, dict):
        return "unknown", {}, []

    status = str(raw.get("status") or "").strip().lower()
    data_obj = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    result_obj = data_obj.get("result") if isinstance(data_obj.get("result"), dict) else {}

    if not status:
        status = str(data_obj.get("status") or result_obj.get("status") or "").strip().lower()

    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = result_obj.get("payload") if isinstance(result_obj.get("payload"), dict) else {}

    failures = payload.get("failures") if isinstance(payload, dict) else None
    if not isinstance(failures, list):
        failures = []

    return status or "unknown", payload, failures


def _extract_media_ids(payload: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _append(media_id: Any) -> None:
        value = str(media_id or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        found.append(value)

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return

        if not isinstance(node, dict):
            return

        media_ids = node.get("media_ids")
        if isinstance(media_ids, list):
            for item in media_ids:
                _append(item)

        candidate_id = str(node.get("id") or "").strip()
        candidate_type = str(node.get("type") or "").strip().lower()
        has_media_signature = any(
            key in node
            for key in ("path", "thumbnail", "validity", "source", "caption", "width", "height")
        )
        if candidate_id and (candidate_type in {"image", "photo", "video", "document", "gif"} or has_media_signature):
            _append(candidate_id)

        for value in node.values():
            _walk(value)

    _walk(payload)
    return found


def _normalise_image_urls(image_urls: list[str] | None) -> list[str]:
    urls = [str(url).strip() for url in (image_urls or []) if str(url).strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


async def _upload_media_from_urls(*, workspace_id: str, image_urls: list[str]) -> list[str]:
    normalized_urls = _normalise_image_urls(image_urls)
    if not normalized_urls:
        return []

    media_rows = [
        {
            "url": url,
            "name": f"social-image-{index + 1}",
        }
        for index, url in enumerate(normalized_urls)
    ]
    payload = {
        "media": media_rows,
        "type": "bulk" if len(media_rows) > 1 else "single",
        "direct_upload": True,
    }

    created = await _request_json(
        method="POST",
        path="/media/from-url",
        workspace_id=workspace_id,
        payload=payload,
    )

    direct_ids = _extract_media_ids(created)
    if direct_ids:
        return direct_ids

    job_id = _resolve_job_id(created)
    if not job_id:
        raise PublerError("Publer media upload did not return a job_id.")

    deadline = time.monotonic() + _MEDIA_UPLOAD_MAX_WAIT_SECONDS
    last_status = "working"

    while time.monotonic() < deadline:
        raw = await _request_json(
            method="GET",
            path=f"/job_status/{job_id}",
            workspace_id=workspace_id,
        )
        last_status, status_payload, failures = _normalise_status_payload(raw)
        media_ids = _extract_media_ids(raw)
        if media_ids:
            return media_ids

        if last_status in {"failed", "error"}:
            raise PublerError(
                "Publer media upload job failed: "
                f"{failures or status_payload or raw}"
            )

        if last_status in {"completed", "complete", "done"}:
            break

        await asyncio.sleep(_MEDIA_UPLOAD_POLL_INTERVAL_SECONDS)

    raise PublerError(
        "Publer media upload did not return media IDs before timeout "
        f"(status={last_status}, timeout={_MEDIA_UPLOAD_MAX_WAIT_SECONDS}s)."
    )


async def list_workspaces() -> list[dict[str, str]]:
    payload = await _request_json(method="GET", path="/workspaces")
    return _normalize_workspaces(payload)


async def list_accounts(workspace_id: str) -> list[dict[str, str]]:
    workspace_id = workspace_id.strip()
    if not workspace_id:
        raise PublerError("workspace_id is required to list accounts.")
    payload = await _request_json(method="GET", path="/accounts", workspace_id=workspace_id)
    return _normalize_accounts(payload)


def filter_accounts_for_text_posts(accounts: list[dict[str, str]]) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for account in accounts:
        provider_raw = str(account.get("provider") or "").strip().lower()
        provider = _PROVIDER_ALIASES.get(provider_raw, provider_raw)
        if provider in _VIDEO_ONLY_PROVIDERS:
            continue
        if provider and provider not in _TEXT_IMAGE_PROVIDERS:
            continue
        filtered.append(account)
    return filtered


async def create_text_post(
    *,
    workspace_id: str,
    account_ids: list[str],
    provider_texts: dict[str, str],
    image_urls: list[str] | None = None,
    mode: str,
    scheduled_at: str = "",
) -> dict[str, Any]:
    workspace_id = workspace_id.strip()
    if not workspace_id:
        raise PublerError("workspace_id is required.")

    normalized_accounts = [str(account_id).strip() for account_id in account_ids if str(account_id).strip()]
    if not normalized_accounts:
        raise PublerError("At least one Publer account must be selected.")

    media_items: list[dict[str, str]] = []
    normalized_image_urls = _normalise_image_urls(image_urls)
    if normalized_image_urls:
        media_ids = await _upload_media_from_urls(
            workspace_id=workspace_id,
            image_urls=normalized_image_urls,
        )
        media_items = [{"id": media_id, "type": "image"} for media_id in media_ids]

    networks: dict[str, dict[str, Any]] = {}
    for provider, text in (provider_texts or {}).items():
        p = str(provider or "").strip().lower()
        provider_key = _PROVIDER_ALIASES.get(p, p)
        t = str(text or "").strip()
        if provider_key not in _TEXT_IMAGE_PROVIDERS:
            continue
        if not provider_key or not t:
            continue
        network_entry: dict[str, Any] = {"type": "status", "text": t}
        if media_items:
            network_entry = {
                "type": "photo",
                "text": t,
                "media": media_items,
            }
        networks[provider_key] = network_entry

    if not networks:
        raise PublerError(
            "No text/image-compatible provider content was provided. "
            "TikTok/YouTube must be handled by the video workflow."
        )

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"draft", "scheduled"}:
        raise PublerError("mode must be one of: draft, scheduled")

    endpoint = "/posts/schedule"
    state = "draft" if normalized_mode == "draft" else "scheduled"

    accounts_payload: list[dict[str, str]] = []
    for account_id in normalized_accounts:
        account_entry: dict[str, str] = {"id": account_id}
        if state == "scheduled" and scheduled_at.strip():
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

    status, payload, failures = _normalise_status_payload(raw)

    return {
        "status": status,
        "payload": payload,
        "failures": failures,
        "raw": raw,
    }
