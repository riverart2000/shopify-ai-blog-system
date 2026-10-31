"""Client for the Social Engine API on the live Python server (localhost only)."""

from __future__ import annotations

from typing import Any

import httpx

from task_agent.config import Settings


class SocialError(RuntimeError):
    """The Social Engine refused or couldn't be reached (message shown to Joe as-is)."""


class SocialClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = settings.social_api_url.rstrip("/")
        self.key = settings.social_api_key.get_secret_value()
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.key)

    async def request(
        self, method: str, path: str, *, body: Any = None, params: dict | None = None, http_timeout: float = 60
    ) -> Any:
        if not self.key:
            raise SocialError("The Social Engine key isn't set (SOCIAL_API_KEY in task-agent/.env).")
        try:
            async with httpx.AsyncClient(timeout=http_timeout, transport=self.transport) as client:
                response = await client.request(
                    method,
                    f"{self.base}{path}",
                    headers={"x-api-key": self.key},
                    json=body,
                    params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                )
        except httpx.HTTPError as exc:
            raise SocialError(f"Couldn't reach the Social Engine: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail") or response.text
            except ValueError:
                detail = response.text
            raise SocialError(f"Social Engine said: {str(detail)[:300]}")
        return (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.content
        )

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: dict | None = None, http_timeout: float = 60) -> Any:
        return await self.request("POST", path, body=body or {}, http_timeout=http_timeout)

    async def media(self, asset_id: str) -> bytes:
        data = await self.request("GET", f"/media/{asset_id}", http_timeout=30)
        if not isinstance(data, bytes | bytearray):
            raise SocialError("The image couldn't be loaded.")
        return bytes(data)
