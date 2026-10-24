"""Postiz creative tools (Team plan) via the Postiz MCP server.

Postiz's AI image generator is only exposed through MCP; video generation,
voices and the media library are used through MCP too so the engine sees the
same generators as the Postiz UI (they are discovered at run time — e.g.
"image-text-slides" with ElevenLabs voices and "seedance" from product photos).

* Every call is ledgered (service "postiz_mcp").
* Monthly credit budgets are enforced locally before spending a credit.
* Credit-spending calls are refused unless live publishing is enabled
  (same guard as posting) so the dev checkout can never use credits.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from db.social_engine import observability

from ..context import StoreContext
from ..errors import ConfigurationError, ExternalServiceError, RateLimited, ValidationError
from ..http import CallContext, ServiceClient
from .guard import LIVE_PUBLISHING_ENV, live_publishing_enabled

logger = logging.getLogger("ai_blog_server.social_engine.publishers")

SERVICE = "postiz_mcp"
_PROTOCOL = "2025-06-18"
_MONTH = 30 * 86400


@dataclass
class GeneratedMedia:
    external_id: str
    url: str
    kind: str            # image | video
    job_id: str = ""
    status: str = "ready"  # ready | pending | failed
    error: str = ""


def _parse_rpc(text: str, content_type: str) -> dict:
    """MCP over streamable HTTP answers with JSON or an SSE stream of JSON-RPC messages."""
    if "text/event-stream" in content_type:
        messages = []
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        return messages[-1] if messages else {}
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def _tool_payload(result: dict) -> Any:
    """Return structuredContent, else the JSON in the first text block, else the text."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [block.get("text", "") for block in result.get("content") or [] if block.get("type") == "text"]
    if texts:
        text = texts[0].strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return text
        return text
    return None


class PostizMCP:
    """Minimal stateless MCP client (JSON-RPC 2.0 over streamable HTTP)."""

    def __init__(self, api_key: str, *, url: str, ctx: CallContext | None = None, timeout: float = 120.0) -> None:
        if not api_key:
            raise ConfigurationError("Postiz API key missing (POSTIZ_API_KEY) — Postiz AI tools are unavailable.")
        self.ctx = ctx or CallContext()
        self._client = ServiceClient(SERVICE, url, timeout=timeout, headers={
            "Authorization": f"Bearer {api_key.removeprefix('Bearer ').strip()}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        self._ids = 0
        self._initialised = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _rpc(self, method: str, params: dict | None, *, operation: str, idempotent: bool) -> dict:
        self._ids += 1
        body = {"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params or {}}
        response = await self._client.request("POST", "", operation=operation, ctx=self.ctx, json=body,
                                              idempotent=idempotent, max_attempts=3 if idempotent else 1)
        message = _parse_rpc(response.text, response.headers.get("content-type", ""))
        if message.get("error"):
            error = message["error"]
            raise ExternalServiceError("postiz", f"MCP {method} failed: {error.get('message', error)}",
                                       status_code=response.status_code, retryable=False)
        return message.get("result") or {}

    async def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        await self._rpc("initialize", {
            "protocolVersion": _PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "bioluxe-social-engine", "version": "1.0"},
        }, operation="mcp.initialize", idempotent=True)
        self._initialised = True

    async def call(self, tool: str, arguments: dict, *, idempotent: bool) -> Any:
        await self._ensure_initialised()
        result = await self._rpc("tools/call", {"name": tool, "arguments": arguments},
                                 operation=f"mcp.{tool}", idempotent=idempotent)
        payload = _tool_payload(result)
        if result.get("isError"):
            raise ExternalServiceError("postiz", f"{tool} failed: {payload}", retryable=False)
        if isinstance(payload, dict) and payload.get("error"):
            raise ExternalServiceError("postiz", f"{tool} failed: {payload['error']}", retryable=False)
        return payload

    async def list_tools(self) -> list[dict]:
        await self._ensure_initialised()
        result = await self._rpc("tools/list", {}, operation="mcp.tools_list", idempotent=True)
        return list(result.get("tools") or [])


async def _used_this_month(operation: str) -> int:
    return await observability.count_api_calls(service=SERVICE, operation=operation,
                                               since=int(time.time()) - _MONTH)


class PostizCreativeTools:
    """Postiz AI images/videos, voices and media library for one store."""

    def __init__(self, sctx: StoreContext, *, correlation_id: str = "", mcp: PostizMCP | None = None) -> None:
        self.sctx = sctx
        self.settings = sctx.settings.postiz
        self.mcp = mcp or PostizMCP(self.settings.api_key(), url=self.settings.mcp_url,
                                    ctx=sctx.call_context(correlation_id))

    async def aclose(self) -> None:
        await self.mcp.aclose()

    def _require_live(self, what: str) -> None:
        if not live_publishing_enabled():
            raise ConfigurationError(
                f"{what} uses Postiz credits and is only allowed on the live system "
                f"({LIVE_PUBLISHING_ENV}=1)."
            )

    async def budget_status(self) -> dict[str, Any]:
        return {
            "images_used": await _used_this_month("mcp.generateImageTool"),
            "images_budget": self.settings.ai_image_monthly_budget,
            "videos_used": await _used_this_month("mcp.generateVideoTool"),
            "videos_budget": self.settings.ai_video_monthly_budget,
        }

    async def generate_image(self, prompt: str) -> GeneratedMedia:
        prompt = " ".join(prompt.split())
        if len(prompt) < 10:
            raise ValidationError("AI image prompt is too short.")
        self._require_live("Postiz AI image generation")
        budget = self.settings.ai_image_monthly_budget
        if budget <= 0:
            raise ConfigurationError("Postiz AI images are disabled (monthly budget is 0).")
        if await _used_this_month("mcp.generateImageTool") >= budget:
            raise RateLimited(f"Postiz AI image budget used ({budget} this month).",
                              retry_at=int(time.time()) + 86400)
        result = await self.mcp.call("generateImageTool", {"prompt": prompt[:1500]}, idempotent=False)
        if not isinstance(result, dict) or not result.get("path"):
            raise ExternalServiceError("postiz", f"generateImageTool returned no image: {result}", retryable=False)
        return GeneratedMedia(external_id=str(result.get("id", "")), url=str(result["path"]), kind="image")

    async def video_generators(self) -> list[dict]:
        result = await self.mcp.call("generateVideoOptions", {}, idempotent=True)
        if isinstance(result, dict):
            return list(result.get("video") or result.get("output") or [])
        return list(result or [])

    async def voices(self) -> list[dict]:
        result = await self.mcp.call("videoFunctionTool", {"identifier": "image-text-slides",
                                                           "functionName": "loadVoices"}, idempotent=True)
        if isinstance(result, list):
            return result
        result = result or {}
        return list(result.get("voices") or result.get("output") or [])

    async def start_video(
        self, *, generator: str, prompt: str, orientation: str = "vertical",
        images: list[dict] | None = None, voice: str = "",
    ) -> GeneratedMedia:
        """Start a Postiz video job. images: Postiz media [{id, path}] (max 3)."""
        self._require_live("Postiz AI video generation")
        budget = self.settings.ai_video_monthly_budget
        if budget <= 0:
            raise ConfigurationError("Postiz AI videos are disabled (monthly budget is 0).")
        if await _used_this_month("mcp.generateVideoTool") >= budget:
            raise RateLimited(f"Postiz AI video budget used ({budget} this month).",
                              retry_at=int(time.time()) + 86400)
        generators = {g.get("type"): g for g in await self.video_generators()}
        if generator not in generators:
            raise ConfigurationError(f"Postiz has no '{generator}' video generator (available: {', '.join(generators)}).")
        params: list[dict] = [{"key": "prompt", "value": prompt[:1500]}]
        if generator == "image-text-slides":
            if not voice:
                offered = await self.voices()
                voice = str((offered[0] if offered else {}).get("id", ""))
            if not voice:
                raise ConfigurationError("No ElevenLabs voice available for image-text-slides.")
            params.append({"key": "voice", "value": voice})
        if images:
            params.append({"key": "images", "value": images[:3]})
        result = await self.mcp.call("generateVideoTool", {
            "identifier": generator, "output": orientation, "customParams": params,
        }, idempotent=False)
        if isinstance(result, dict) and result.get("url"):
            return GeneratedMedia(external_id=str(result.get("id", "")), url=result["url"], kind="video")
        job_id = str((result or {}).get("jobId") or (result or {}).get("id") or "") if isinstance(result, dict) else ""
        if not job_id:
            raise ExternalServiceError("postiz", f"generateVideoTool returned no job id: {result}", retryable=False)
        return GeneratedMedia(external_id="", url="", kind="video", job_id=job_id, status="pending")

    async def video_status(self, job_id: str) -> GeneratedMedia:
        result = await self.mcp.call("videoStatusTool", {"jobId": job_id}, idempotent=True) or {}
        status = str(result.get("status", "pending"))
        return GeneratedMedia(external_id=str(result.get("id", "")), url=str(result.get("url", "")), kind="video",
                              job_id=job_id, status="ready" if status == "completed" else status,
                              error=str(result.get("error", "")))

    async def upload_from_url(self, url: str) -> dict:
        """Put a public image (e.g. Shopify CDN product photo) into the Postiz media library."""
        self._require_live("Uploading to the Postiz media library")
        result = await self.mcp.call("uploadFromUrlTool", {"url": url}, idempotent=True)
        if not isinstance(result, dict) or not result.get("path"):
            raise ExternalServiceError("postiz", f"uploadFromUrlTool returned no media: {result}", retryable=False)
        return {"id": str(result.get("id", "")), "path": str(result["path"])}

    async def media_library(self, *, search: str = "", page: int = 1) -> dict:
        """Designs/media in the Postiz library (e.g. made with the Postiz design editor templates)."""
        args: dict[str, Any] = {"page": max(1, page)}
        if search:
            args["search"] = search
        result = await self.mcp.call("mediaListTool", args, idempotent=True) or {}
        return {"pages": int(result.get("pages") or 1), "items": list(result.get("output") or [])}


def get_creative_tools(sctx: StoreContext, *, correlation_id: str = "") -> PostizCreativeTools:
    return PostizCreativeTools(sctx, correlation_id=correlation_id)
