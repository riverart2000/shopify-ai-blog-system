"""providers/openai_provider.py — OpenAI text and image generation (GPT-4o, DALL-E)."""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from .base import ImageProvider, ProviderError, TextProvider
from .deepseek import _build_user_prompt, _norm_hashtags, _norm_long_tail, _parse_json, _validate
from utils import clean_title, log_debug_payload

logger = logging.getLogger("ai_blog_server")

_DEFAULT_TEXT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_DEFAULT_IMAGE_ENDPOINT = "https://api.openai.com/v1/images/generations"
_DEFAULT_SYSTEM = (
    "You are an expert content writer. "
    "Write detailed, engaging, SEO-optimised blog posts."
)


def _chat_endpoint(base: str) -> str:
    """If `base` is a bare origin (no meaningful path), append /v1/chat/completions."""
    p = urlparse(base)
    if not p.path or p.path in ("", "/"):
        return urlunparse(p._replace(path="/v1/chat/completions"))
    return base


class OpenAITextProvider(TextProvider):

    async def generate_text(self, prompt: str, system_prompt: str = "", prompt_ending: str = "") -> dict:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("OpenAI API key is not configured", retryable=False)

        extra = self.model.extra
        endpoint = _chat_endpoint(self.model.endpoint) if self.model.endpoint else _DEFAULT_TEXT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 90))
        max_retries = int(extra.get("max_retries", 2))

        user_prompt = _build_user_prompt(prompt, prompt_ending)
        payload = {
            "model": self.model.model_name or "gpt-4o-mini",
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        last_error: Optional[Exception] = None
        attempts = max_retries + 1

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(attempts):
                try:
                    logger.debug(
                        "OpenAI attempt %d/%d model=%s store=%s",
                        attempt + 1, attempts, self.model.model_name, self.model.store_id,
                    )
                    logger.debug(
                        "OpenAI prompt sent →\n[system]\n%s\n[user]\n%s",
                        sys, user_prompt,
                    )
                    log_debug_payload(logger, f"OpenAI text → payload (attempt {attempt + 1})", payload)
                    resp = await client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    resp.raise_for_status()
                    log_debug_payload(logger, "OpenAI text ← response", resp.json())
                    raw = resp.json()["choices"][0]["message"]["content"]
                    logger.debug("OpenAI response received ←\n%s", raw)
                    data = _parse_json(raw)
                    _validate(data)
                    data["title"] = clean_title(data.get("title", ""))
                    data["keywords"] = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
                    data["hashtags"] = _norm_hashtags(data.get("hashtags", []))
                    data["long_tail_keywords"] = _norm_long_tail(data.get("long_tail_keywords", []))
                    data["pin_description"] = str(data.get("pin_description", "") or "").strip()
                    return data
                except (ValueError, KeyError) as exc:
                    last_error = exc
                except httpx.TimeoutException as exc:
                    last_error = exc
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code in (401, 403):
                        raise ProviderError("OpenAI auth error — check API key", retryable=False) from exc
                    err_body = ""
                    try:
                        err_body = exc.response.json().get("error", {}).get("message") or exc.response.text
                    except Exception:
                        err_body = exc.response.text
                    last_error = ProviderError(f"HTTP {code}: {err_body[:400]}")
                    logger.warning("OpenAI HTTP %d attempt %d: %s", code, attempt + 1, err_body[:200])

        raise ProviderError(f"OpenAI text failed after {attempts} attempts: {last_error}")

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("OpenAI API key is not configured", retryable=False)

        extra = self.model.extra
        endpoint = _chat_endpoint(self.model.endpoint) if self.model.endpoint else _DEFAULT_TEXT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 90))

        payload = {
            "model": self.model.model_name or "gpt-4o-mini",
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": prompt},
            ],
        }
        log_debug_payload(logger, "OpenAI text raw → payload", payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if not resp.is_success:
                err_body = ""
                try:
                    err_body = resp.json().get("error", {}).get("message") or resp.text
                except Exception:
                    err_body = resp.text
                raise ProviderError(
                    f"HTTP {resp.status_code} from {endpoint}: {err_body[:400]}"
                )
            body = resp.json()
            log_debug_payload(logger, "OpenAI text raw ← response", body)
            return body["choices"][0]["message"]["content"]


class OpenAIImageProvider(ImageProvider):

    async def generate_images(
        self,
        image_prompt: str,
        count: int = 2,
        reference_image: str | None = None,
    ) -> list[str]:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("OpenAI API key is not configured", retryable=False)

        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_IMAGE_ENDPOINT
        timeout = float(extra.get("timeout", 60))
        n = min(int(extra.get("image_count", count)), 10)
        size = extra.get("size", "1024x1024")

        payload = {
            "model": self.model.model_name or "dall-e-3",
            "prompt": image_prompt,
            "n": n,
            "size": size,
        }

        model_name = (self.model.model_name or "").strip().lower()
        if "dall-e-3" in model_name:
            # Prefer the higher-fidelity settings for realistic ecommerce images.
            payload["quality"] = extra.get("quality", "hd")
            payload["style"] = extra.get("style", "natural")
        elif "gpt-image" in model_name:
            # GPT Image uses a different quality scale than DALL·E.
            payload["quality"] = extra.get("quality", "high")
            if reference_image:
                # GPT Image supports conditioning with image URLs/base64 inputs.
                payload["image"] = reference_image

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                logger.debug(
                    "OpenAI image request model=%s count=%d prompt=%s",
                    self.model.model_name or "dall-e-3", n, image_prompt,
                )
                log_debug_payload(logger, "OpenAI image → payload", payload)
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
                log_debug_payload(logger, "OpenAI image ← response", body)
                urls = [item["url"] for item in body.get("data", []) if item.get("url")]
                logger.debug("OpenAI image returned %d URL(s): %s", len(urls), urls)
                return urls
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise ProviderError("OpenAI image auth error", retryable=False) from exc
            raise ProviderError(f"OpenAI image HTTP {code}") from exc
        except Exception as exc:
            raise ProviderError(f"OpenAI image error: {exc}") from exc
