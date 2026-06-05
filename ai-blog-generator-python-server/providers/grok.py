"""providers/grok.py — Grok (xAI) image generation."""
from __future__ import annotations

import logging

import httpx

from .base import ImageProvider, ProviderError
from utils import log_debug_payload

logger = logging.getLogger("ai_blog_server")

_DEFAULT_ENDPOINT = "https://api.x.ai/v1/images/generations"


class GrokProvider(ImageProvider):

    async def generate_images(self, image_prompt: str, count: int = 2) -> list[str]:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("Grok API key is not configured", retryable=False)

        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_ENDPOINT
        timeout = float(extra.get("timeout", 60))
        n = int(extra.get("image_count", count))

        payload = {
            "model": self.model.model_name or "grok-2-image",
            "prompt": image_prompt,
            "n": n,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                logger.debug(
                    "Grok image request model=%s count=%d prompt=%s",
                    self.model.model_name or "grok-2-image", n, image_prompt,
                )
                log_debug_payload(logger, "Grok image → payload", payload)
                resp = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                body = resp.json()
                log_debug_payload(logger, "Grok image ← response", body)
                urls = [item["url"] for item in body.get("data", []) if item.get("url")]
                logger.debug(
                    "Grok image returned %d URL(s) for store=%s: %s",
                    len(urls), self.model.store_id, urls,
                )
                return urls
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise ProviderError(f"Grok auth error {code}", retryable=False) from exc
            raise ProviderError(
                f"Grok HTTP {code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Grok timed out after {timeout}s") from exc
        except Exception as exc:
            raise ProviderError(f"Grok error: {exc}") from exc
