"""providers/deepseek.py — DeepSeek text generation."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from .base import ModelRecord, ProviderError, TextProvider
from utils import log_debug_payload

logger = logging.getLogger("ai_blog_server")

_DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
_DEFAULT_SYSTEM = (
    "You are an expert content writer. "
    "Write detailed, engaging, SEO-optimised blog posts."
)

_JSON_CONTRACT = [
    '"title": string — compelling SEO blog title',
    '"summary": string — 2-3 sentence meta description',
    '"keywords": array of strings — 5-8 SEO keywords',
    '"hashtags": array of strings — 5 hashtags with # prefix',
    ('"content": string — full blog body as plain text. '
     'Use ## for section headings and - for bullet points. No HTML tags. '
     'Do NOT repeat the title at the start of the content.'),
]

_DEFAULT_PROMPT_ENDING = (
    "Return ONLY a single valid JSON object with exactly these fields:\n"
    + "\n".join(f"  {line}" for line in _JSON_CONTRACT)
    + "\n\nNo markdown fences. No explanation. Raw JSON only."
)


class DeepSeekProvider(TextProvider):

    async def generate_text(self, prompt: str, system_prompt: str = "", prompt_ending: str = "") -> dict:
        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 90))
        max_retries = int(extra.get("max_retries", 2))

        user_prompt = _build_user_prompt(prompt, prompt_ending)
        payload = {
            "model": self.model.model_name or "deepseek-chat",
            "temperature": temperature,
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
                        "DeepSeek attempt %d/%d model=%s store=%s",
                        attempt + 1, attempts, self.model.model_name, self.model.store_id,
                    )
                    logger.debug(
                        "DeepSeek prompt sent →\n[system]\n%s\n[user]\n%s",
                        sys, user_prompt,
                    )
                    log_debug_payload(logger, f"DeepSeek → payload (attempt {attempt + 1})", payload)
                    resp = await client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self.model.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    resp.raise_for_status()
                    log_debug_payload(logger, "DeepSeek ← response", resp.json())
                    raw = resp.json()["choices"][0]["message"]["content"]
                    data = _parse_json(raw)
                    _validate(data)
                    data["keywords"] = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
                    data["hashtags"] = _norm_hashtags(data.get("hashtags", []))
                    return data
                except (ValueError, KeyError) as exc:
                    last_error = exc
                    logger.warning("DeepSeek parse error attempt %d: %s", attempt + 1, exc)
                except httpx.TimeoutException as exc:
                    last_error = exc
                    logger.warning("DeepSeek timeout attempt %d", attempt + 1)
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code in (401, 403):
                        raise ProviderError(
                            f"DeepSeek auth error {code} — check API key", retryable=False
                        ) from exc
                    last_error = exc
                    logger.warning("DeepSeek HTTP %d attempt %d", code, attempt + 1)

        raise ProviderError(f"DeepSeek failed after {attempts} attempts: {last_error}")

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        extra = self.model.extra
        endpoint = self.model.endpoint or _DEFAULT_ENDPOINT
        sys = system_prompt or extra.get("system_prompt") or _DEFAULT_SYSTEM
        temperature = float(extra.get("temperature", 0.7))
        timeout = float(extra.get("timeout", 90))

        payload = {
            "model": self.model.model_name or "deepseek-chat",
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": prompt},
            ],
        }
        log_debug_payload(logger, "DeepSeek raw → payload", payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.model.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            body = resp.json()
            log_debug_payload(logger, "DeepSeek raw ← response", body)
            return body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Helpers (also imported by openai_provider.py and local.py)
# ---------------------------------------------------------------------------

def _build_user_prompt(prompt: str, prompt_ending: str = "") -> str:
    ending = prompt_ending.strip() if prompt_ending and prompt_ending.strip() else _DEFAULT_PROMPT_ENDING
    return f"User request: {prompt}\n\n{ending}"


def _parse_json(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```json\s*", "", s)
    s = re.sub(r"^```\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response: {raw[:200]}")


def _validate(data: dict) -> None:
    for field in ("title", "summary", "content"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"Field '{field}' is missing or empty")
    for field in ("keywords", "hashtags"):
        if not isinstance(data.get(field), list):
            raise ValueError(f"Field '{field}' must be an array")


def _norm_hashtags(tags: list) -> list[str]:
    result = []
    for tag in tags:
        t = str(tag).strip()
        if t and not t.startswith("#"):
            t = "#" + t.replace(" ", "")
        if t:
            result.append(t)
    return result
