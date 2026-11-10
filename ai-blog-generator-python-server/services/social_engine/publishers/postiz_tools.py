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

import asyncio
import json
import logging
import re
import time
from datetime import datetime
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
# A Postiz AI image normally arrives in 11–38 s; the ones that fail hang until any timeout (2 or 5 minutes), so
# waiting longer doesn't help. Give up after 90 s, then look for the image in the media library in case it landed.
IMAGE_TIMEOUT_SECONDS = 90.0
LATE_IMAGE_RECHECK_SECONDS = 30.0
_AI_IMAGE_PATH = re.compile(r"^https://uploads\.postiz\.com/[A-Za-z0-9]{10}\.(?:png|jpe?g|webp)$")


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


AGENT_TOOL = "ask_postiz"


class PostizMCP:
    """Minimal stateless MCP client (JSON-RPC 2.0 over streamable HTTP).

    Image generation takes 2–4 minutes on Postiz Cloud, so the HTTP timeout is
    generous; every call is still ledgered whether or not it returns in time."""

    def __init__(self, api_key: str, *, url: str, ctx: CallContext | None = None, timeout: float = 300.0) -> None:
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

    async def _rpc(
        self, method: str, params: dict | None, *, operation: str, idempotent: bool, timeout: float | None = None,
    ) -> dict:
        self._ids += 1
        body = {"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params or {}}
        response = await self._client.request("POST", "", operation=operation, ctx=self.ctx, json=body,
                                              idempotent=idempotent, max_attempts=3 if idempotent else 1,
                                              timeout=timeout)
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

    async def call(self, tool: str, arguments: dict, *, idempotent: bool, timeout: float | None = None) -> Any:
        await self._ensure_initialised()
        result = await self._rpc("tools/call", {"name": tool, "arguments": arguments},
                                 operation=f"mcp.{tool}", idempotent=idempotent, timeout=timeout)
        payload = _tool_payload(result)
        if result.get("isError"):
            raise ExternalServiceError("postiz", f"{tool} failed: {payload}", retryable=False)
        if isinstance(payload, dict) and payload.get("error"):
            raise ExternalServiceError("postiz", f"{tool} failed: {payload['error']}", retryable=False)
        return payload

    async def ask_agent(self, message: str, *, timeout: float) -> tuple[str, dict]:
        """Ask Postiz's own AI agent (the one behind "Generate with AI" in its composer) for text.

        The agent can also schedule posts. It is told not to, and a reply in which it used any tool is refused,
        so nothing it writes can reach a channel except through the engine. Returns (text, token usage)."""
        result = await self.call(AGENT_TOOL, {"message": message}, idempotent=False, timeout=timeout)
        if not isinstance(result, dict):
            raise ExternalServiceError("postiz", f"The Postiz agent returned no text: {str(result)[:200]}", retryable=True)
        used = [str(call.get("toolName") or call.get("payload", {}).get("toolName") or "a tool")
                for step in result.get("steps") or [] if isinstance(step, dict)
                for call in step.get("toolCalls") or [] if isinstance(call, dict)]
        if used:
            raise ExternalServiceError(
                "postiz", f"The Postiz agent used {', '.join(used)} instead of only writing; its reply was discarded. "
                "Check the Postiz calendar for a post it may have made.", retryable=False)
        text = str(result.get("text") or "").strip()
        if not text:
            raise ExternalServiceError("postiz", "The Postiz agent returned an empty reply.", retryable=True)
        return text, dict(result.get("usage") or {})

    async def list_tools(self) -> list[dict]:
        await self._ensure_initialised()
        result = await self._rpc("tools/list", {}, operation="mcp.tools_list", idempotent=True)
        return list(result.get("tools") or [])


async def _used_this_month(operation: str, store_id: str = "") -> int:
    """Credit-spending calls in the last 30 days, including ones that timed out (Postiz may still have charged).

    Counted per brand: each brand's monthly budget is its own share of the Postiz account's allowance."""
    return await observability.count_api_calls(service=SERVICE, operation=operation, store_id=store_id,
                                               since=int(time.time()) - _MONTH, reached_server_only=False)


def _timed_out(exc: BaseException) -> bool:
    return "timeout" in f"{type(exc).__name__} {exc} {exc.__cause__!r}".lower()


def _created_at(item: dict) -> float | None:
    try:
        return datetime.fromisoformat(str(item.get("createdAt") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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
            "images_used": await _used_this_month("mcp.generateImageTool", self.sctx.store_id),
            "images_budget": self.settings.ai_image_monthly_budget,
            "videos_used": await _used_this_month("mcp.generateVideoTool", self.sctx.store_id),
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
        if await _used_this_month("mcp.generateImageTool", self.sctx.store_id) >= budget:
            raise RateLimited(f"Postiz AI image budget used ({budget} this month).",
                              retry_at=int(time.time()) + 86400)
        started = time.time()
        try:
            result = await self.mcp.call("generateImageTool", {"prompt": prompt[:1500]}, idempotent=False,
                                         timeout=IMAGE_TIMEOUT_SECONDS)
        except ExternalServiceError as exc:
            if not _timed_out(exc):
                raise
            late = await self._late_image(started)
            if late is not None:
                logger.info("Postiz AI image arrived late; found it in the media library: %s", late.url)
                return late
            raise ExternalServiceError(
                "postiz", f"Postiz AI image didn't arrive within {int(IMAGE_TIMEOUT_SECONDS)} s or in the media "
                "library (counted against the monthly budget in case Postiz charged it).", retryable=False,
            ) from exc
        if not isinstance(result, dict) or not result.get("path"):
            raise ExternalServiceError("postiz", f"generateImageTool returned no image: {result}", retryable=False)
        return GeneratedMedia(external_id=str(result.get("id", "")), url=str(result["path"]), kind="image")

    async def _late_image(self, started: float) -> GeneratedMedia | None:
        """An AI image made since `started` that reached the media library after the request gave up."""
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(LATE_IMAGE_RECHECK_SECONDS)
            try:
                library = await self.media_library(page=1)
            except Exception as exc:  # the library being unavailable just means no recovery
                logger.info("Couldn't look for a late Postiz AI image: %s", exc)
                return None
            fresh = [item for item in library["items"] if isinstance(item, dict)
                     and _AI_IMAGE_PATH.match(str(item.get("path") or ""))
                     and (_created_at(item) or 0) >= started - 5]
            if fresh:
                newest = max(fresh, key=lambda item: _created_at(item) or 0)
                return GeneratedMedia(external_id=str(newest.get("id", "")), url=str(newest["path"]), kind="image")
        return None

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
        if await _used_this_month("mcp.generateVideoTool", self.sctx.store_id) >= budget:
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
