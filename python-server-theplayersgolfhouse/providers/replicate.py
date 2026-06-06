"""providers/replicate.py — Replicate image and text generation (polling-based)."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .base import ImageProvider, ProviderError, TextProvider
from .deepseek import _build_user_prompt, _parse_json, _validate, _norm_hashtags, _norm_long_tail
from utils import clean_title, log_debug_payload

logger = logging.getLogger("ai_blog_server")

_PREDICTIONS_URL = "https://api.replicate.com/v1/predictions"
_POLL_INTERVAL = 5
_MAX_POLLS = 60   # 5 min total (large reasoning models like DeepSeek R1 can take 3-4 min)


class ReplicateImageProvider(ImageProvider):
    """Replicate image generation.

    Supports two modes (mirroring the text provider):
    - Versioned:     set ``version`` in extra_json
    - Path-based:    set ``endpoint`` to ``https://replicate.com/{owner}/{name}``
                     (e.g. ``https://replicate.com/black-forest-labs/flux-schnell``)
    """

    async def generate_images(self, image_prompt: str, count: int = 2) -> list[str]:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("Replicate API token is not configured", retryable=False)

        extra = self.model.extra
        timeout = float(extra.get("timeout", 120))
        version = extra.get("version", "")

        # Support path-based (official) models via endpoint URL
        endpoint = self.model.endpoint or ""
        _m = re.match(r"https?://replicate\.com/([^/]+/[^/?#]+)", endpoint)
        model_path = _m.group(1) if _m else ""

        if not version and not model_path:
            raise ProviderError(
                "Replicate image provider requires either 'version' in extra_json "
                "or an endpoint in the form https://replicate.com/{owner}/{name}",
                retryable=False,
            )

        input_params: dict = dict(extra.get("input", {}) or {})
        input_params["prompt"] = image_prompt

        if version:
            predictions_url = _PREDICTIONS_URL
            payload: dict = {"version": version, "input": input_params}
        else:
            predictions_url = f"https://api.replicate.com/v1/models/{model_path}/predictions"
            payload = {"input": input_params}

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                logger.debug(
                    "Replicate image request model=%s count=%d prompt=%s",
                    self.model.name, count, image_prompt,
                )
                log_debug_payload(logger, "Replicate image → payload", payload)
                resp = await client.post(predictions_url, headers=headers, json=payload)
                resp.raise_for_status()
                prediction = resp.json()
                pred_id = prediction["id"]
                log_debug_payload(logger, f"Replicate image ← prediction started ({pred_id})", prediction)

                # If the model returned synchronously (Prefer: wait), extract output now
                if prediction.get("status") == "succeeded":
                    output = prediction.get("output", [])
                    if isinstance(output, str):
                        output = [output]
                    urls = [url for url in (output or []) if url]
                    logger.info(
                        "Replicate image (sync) returned %d URL(s) model=%s store=%s",
                        len(urls), self.model.name, self.model.store_id,
                    )
                    return urls

                # Otherwise poll
                logger.debug("Replicate image prediction %s — polling", pred_id)
                poll_url = f"{_PREDICTIONS_URL}/{pred_id}"

                for _ in range(_MAX_POLLS):
                    await asyncio.sleep(_POLL_INTERVAL)
                    poll_resp = await client.get(poll_url, headers=headers)
                    poll_resp.raise_for_status()
                    data = poll_resp.json()
                    status = data.get("status")
                    if status == "succeeded":
                        output = data.get("output", [])
                        if isinstance(output, str):
                            output = [output]
                        urls = [url for url in (output or []) if url]
                        log_debug_payload(logger, f"Replicate image ← succeeded ({pred_id})", data)
                        logger.info(
                            "Replicate image returned %d URL(s) model=%s store=%s",
                            len(urls), self.model.name, self.model.store_id,
                        )
                        return urls
                    if status == "failed":
                        raise ProviderError(
                            f"Replicate prediction failed: {data.get('error', 'unknown')}"
                        )

                raise ProviderError(
                    f"Replicate prediction {pred_id} timed out after {_MAX_POLLS * _POLL_INTERVAL}s"
                )

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise ProviderError("Replicate auth error — check API token", retryable=False) from exc
            raise ProviderError(f"Replicate HTTP {code}") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Replicate error: {exc}") from exc


_DEFAULT_SYSTEM = (
    "You are an expert content writer. "
    "Write detailed, engaging, SEO-optimised blog posts."
)


class ReplicateTextProvider(TextProvider):
    """Replicate-hosted text models (e.g. Claude, DeepSeek, Gemini via Replicate).

    extra_json fields:
      version   (required) — Replicate model version hash
      system    (optional) — override system prompt
      max_tokens (optional, default 4096)
      temperature (optional, default 0.7)
      timeout   (optional, default 120)
    """

    async def generate_text(self, prompt: str, system_prompt: str = "", prompt_ending: str = "") -> dict:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("Replicate API token is not configured", retryable=False)

        extra = self.model.extra
        version = extra.get("version", "")

        # Derive owner/name from endpoint URL: https://replicate.com/{owner}/{name}
        endpoint = self.model.endpoint or ""
        _m = re.match(r"https?://replicate\.com/([^/]+/[^/?#]+)", endpoint)
        model_path = _m.group(1) if _m else ""

        if not version and not model_path:
            raise ProviderError(
                "Replicate text provider requires an endpoint in the form "
                "https://replicate.com/{owner}/{name}, or 'version' in extra_json",
                retryable=False,
            )

        timeout = float(extra.get("timeout", 120))
        system = system_prompt or extra.get("system") or _DEFAULT_SYSTEM
        user_prompt = _build_user_prompt(prompt, prompt_ending)

        input_params: dict = {
            "prompt": user_prompt,
            "system_prompt": system,
            "max_tokens": int(extra.get("max_tokens", 4096)),
            "temperature": float(extra.get("temperature", 0.7)),
        }
        # Allow caller to override/extend input fields via extra_json["input"]
        input_params.update(extra.get("input", {}) or {})

        # Versioned model: POST /v1/predictions with {"version": ..., "input": ...}
        # Official model:  POST /v1/models/{owner}/{name}/predictions with {"input": ...}
        if version:
            predictions_url = _PREDICTIONS_URL
            payload: dict = {"version": version, "input": input_params}
        else:
            predictions_url = f"https://api.replicate.com/v1/models/{model_path}/predictions"
            payload = {"input": input_params}

        logger.debug(
            "Replicate text prompt sent → %s\n[system]\n%s\n[user]\n%s",
            predictions_url, system, user_prompt,
        )

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        }

        max_retries = int(extra.get("max_retries", 2))
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                raw = await self._run_prediction(predictions_url, payload, headers, timeout)
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
                logger.warning(
                    "Replicate text parse error attempt %d/%d: %s",
                    attempt + 1, max_retries + 1, exc,
                )
            except ProviderError:
                raise

        raise ProviderError(
            f"Replicate text failed after {max_retries + 1} attempts: {last_error}"
        )

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        api_key = self.model.resolved_api_key
        if not api_key:
            raise ProviderError("Replicate API token is not configured", retryable=False)

        extra = self.model.extra
        version = extra.get("version", "")
        endpoint = self.model.endpoint or ""
        _m = re.match(r"https?://replicate\.com/([^/]+/[^/?#]+)", endpoint)
        model_path = _m.group(1) if _m else ""

        if not version and not model_path:
            raise ProviderError(
                "Replicate text provider requires an endpoint or 'version' in extra_json",
                retryable=False,
            )

        timeout = float(extra.get("timeout", 120))
        sys = system_prompt or extra.get("system") or _DEFAULT_SYSTEM

        input_params: dict = {
            "prompt": prompt,
            "system_prompt": sys,
            "max_tokens": int(extra.get("max_tokens", 4096)),
            "temperature": float(extra.get("temperature", 0.7)),
        }
        input_params.update(extra.get("input", {}) or {})

        if version:
            predictions_url = _PREDICTIONS_URL
            payload: dict = {"version": version, "input": input_params}
        else:
            predictions_url = f"https://api.replicate.com/v1/models/{model_path}/predictions"
            payload = {"input": input_params}

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        }
        return await self._run_prediction(predictions_url, payload, headers, timeout)

    async def _run_prediction(self, predictions_url: str, payload: dict, headers: dict, timeout: float) -> str:
        async with httpx.AsyncClient(timeout=timeout) as client:
            log_debug_payload(logger, "Replicate text → payload", payload)
            resp = await client.post(predictions_url, headers=headers, json=payload)
            resp.raise_for_status()
            pred_id = resp.json()["id"]
            log_debug_payload(logger, f"Replicate text ← prediction started ({pred_id})", resp.json())

            poll_url = f"{_PREDICTIONS_URL}/{pred_id}"
            for _ in range(_MAX_POLLS):
                await asyncio.sleep(_POLL_INTERVAL)
                poll_resp = await client.get(poll_url, headers=headers)
                poll_resp.raise_for_status()
                data = poll_resp.json()
                status = data.get("status")
                if status == "succeeded":
                    output = data.get("output", "")
                    # Replicate text models return output as a list of tokens or a single string
                    if isinstance(output, list):
                        raw = "".join(str(t) for t in output)
                    else:
                        raw = str(output or "")
                    log_debug_payload(logger, f"Replicate text ← succeeded ({pred_id})", data)
                    logger.debug("Replicate text response received ←\n%s", raw)
                    return raw
                if status == "failed":
                    raise ProviderError(
                        f"Replicate prediction failed: {data.get('error', 'unknown')}"
                    )

            raise ProviderError(
                f"Replicate prediction {pred_id} timed out after {_MAX_POLLS * _POLL_INTERVAL}s"
            )

