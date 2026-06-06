"""providers/local.py — Ollama / LM Studio compatible text generation."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from .base import ModelRecord, ProviderError, TextProvider
from .deepseek import _build_user_prompt, _norm_hashtags, _norm_long_tail, _parse_json, _validate
from utils import clean_title, log_debug_payload

logger = logging.getLogger("ai_blog_server")

_DEFAULT_ENDPOINT = "http://localhost:11434/api/chat"
_DEFAULT_SYSTEM = (
    "You are an expert content writer. "
    "Write detailed, engaging, SEO-optimised blog posts."
)


class OllamaProvider(TextProvider):

    async def generate_text(self, prompt: str, system_prompt: str = "", prompt_ending: str = "") -> dict:
        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 180))
        max_retries = int(extra.get("max_retries", 1))

        user_prompt = _build_user_prompt(prompt, prompt_ending)
        payload = {
            "model": self.model.model_name or "llama3",
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user_prompt},
            ],
        }

        last_error: Optional[Exception] = None
        attempts = max_retries + 1

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(attempts):
                try:
                    logger.debug(
                        "Ollama attempt %d/%d model=%s store=%s",
                        attempt + 1, attempts, self.model.model_name, self.model.store_id,
                    )
                    logger.debug(
                        "Ollama prompt sent →\n[system]\n%s\n[user]\n%s",
                        sys, user_prompt,
                    )
                    log_debug_payload(logger, f"Ollama → payload (attempt {attempt + 1})", payload)
                    resp = await client.post(endpoint, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                    log_debug_payload(logger, "Ollama ← response", body)
                    raw = (
                        body.get("message", {}).get("content")
                        or body.get("response", "")
                    )
                    logger.debug("Ollama response received ←\n%s", raw)
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
                    logger.warning("Ollama timeout attempt %d", attempt + 1)
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    last_error = exc
                    logger.warning("Ollama HTTP %d attempt %d", code, attempt + 1)
                except httpx.ConnectError as exc:
                    raise ProviderError(
                        f"Cannot connect to local model at {endpoint} — is Ollama running?",
                        retryable=False,
                    ) from exc

        raise ProviderError(f"Ollama failed after {attempts} attempts: {last_error}")

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 180))

        payload = {
            "model": self.model.model_name or "llama3",
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": prompt},
            ],
        }
        log_debug_payload(logger, "Ollama raw → payload", payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(endpoint, json=payload)
                resp.raise_for_status()
                body = resp.json()
                log_debug_payload(logger, "Ollama raw ← response", body)
                return body.get("message", {}).get("content") or body.get("response", "")
            except httpx.ConnectError as exc:
                raise ProviderError(
                    f"Cannot connect to local model at {endpoint} — is Ollama running?",
                    retryable=False,
                ) from exc
