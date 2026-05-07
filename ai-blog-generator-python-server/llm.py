"""
llm.py — DeepSeek text generation and Grok image generation.
All external AI calls are isolated here.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from config import DeepSeekConfig, GrokConfig

logger = logging.getLogger("ai_blog_server")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class BlogGenerationError(Exception):
    pass


class BlogContent:
    def __init__(
        self,
        title: str,
        summary: str,
        keywords: list[str],
        hashtags: list[str],
        content: str,
        image_b64_list: list[str],
    ) -> None:
        self.title = title
        self.summary = summary
        self.keywords = keywords
        self.hashtags = hashtags
        self.content = content
        self.image_b64_list = image_b64_list


# ---------------------------------------------------------------------------
# DeepSeek text generation
# ---------------------------------------------------------------------------

_JSON_CONTRACT = [
    '"title": string — compelling SEO blog title',
    '"summary": string — 2-3 sentence meta description',
    '"keywords": array of strings — 5-8 SEO keywords',
    '"hashtags": array of strings — 5 hashtags with # prefix',
    '"content": string — full blog body as plain text. Use ## for section headings and - for bullet points. No HTML tags.',
]

_USER_PROMPT_TEMPLATE = """\
User request: {prompt}

Return ONLY a single valid JSON object with exactly these fields:
{contract}

No markdown fences. No explanation. Raw JSON only.
"""


def _build_user_prompt(prompt: str) -> str:
    contract = "\n".join(f"  {line}" for line in _JSON_CONTRACT)
    return _USER_PROMPT_TEMPLATE.format(prompt=prompt, contract=contract)


def _strip_fences(raw: str) -> str:
    stripped = raw.strip()
    stripped = re.sub(r"^```json\s*", "", stripped)
    stripped = re.sub(r"^```\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _parse_blog_json(raw: str) -> dict:
    cleaned = _strip_fences(raw)

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try extracting the outermost JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise BlogGenerationError(
        f"Could not parse JSON from model response. Raw excerpt: {raw[:200]}"
    )


def _validate_blog_dict(data: dict) -> None:
    for field in ("title", "summary", "content"):
        value = data.get(field, "")
        if not isinstance(value, str) or not value.strip():
            raise BlogGenerationError(f"Generated blog field '{field}' is missing or empty.")
    for field in ("keywords", "hashtags"):
        value = data.get(field, [])
        if not isinstance(value, list):
            raise BlogGenerationError(f"Generated blog field '{field}' must be an array.")


def _normalize_hashtags(tags: list) -> list[str]:
    result = []
    for tag in tags:
        t = str(tag).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.replace(" ", "")
        result.append(t)
    return result


def text_to_html(text: str) -> str:
    """
    Convert plain text (## headings, - bullets) to minimal HTML
    suitable for a Shopify article body.
    """
    lines = text.splitlines()
    html_parts: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{stripped[3:].strip()}</h2>")

        elif stripped.startswith("# "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{stripped[2:].strip()}</h2>")

        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{stripped[2:].strip()}</li>")

        elif stripped == "":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            # blank line becomes a paragraph break — skip, next content opens <p>

        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{stripped}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


async def generate_text(config: DeepSeekConfig, prompt: str) -> dict:
    """
    Call DeepSeek and return parsed blog dict.
    Retries up to config.max_retries times on parse failures or timeouts.
    """
    user_prompt = _build_user_prompt(prompt)
    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    last_error: Optional[Exception] = None
    total_attempts = config.max_retries + 1

    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        for attempt in range(total_attempts):
            try:
                logger.debug(
                    "DeepSeek request attempt %d/%d model=%s",
                    attempt + 1,
                    total_attempts,
                    config.model,
                )
                response = await client.post(
                    config.endpoint,
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                raw_content = body["choices"][0]["message"]["content"]
                logger.debug("DeepSeek raw response length=%d", len(raw_content))

                data = _parse_blog_json(raw_content)
                _validate_blog_dict(data)

                data["keywords"] = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
                data["hashtags"] = _normalize_hashtags(data.get("hashtags", []))
                return data

            except BlogGenerationError as exc:
                logger.warning("Attempt %d parse error: %s", attempt + 1, exc)
                last_error = exc
            except httpx.TimeoutException as exc:
                logger.warning("Attempt %d timed out: %s", attempt + 1, exc)
                last_error = BlogGenerationError(f"DeepSeek request timed out: {exc}")
            except httpx.HTTPStatusError as exc:
                logger.error("DeepSeek HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
                raise BlogGenerationError(
                    f"DeepSeek returned HTTP {exc.response.status_code}."
                ) from exc

    raise BlogGenerationError(
        f"DeepSeek failed after {total_attempts} attempts: {last_error}"
    )


# ---------------------------------------------------------------------------
# Grok image generation
# ---------------------------------------------------------------------------

async def generate_images(config: GrokConfig, title: str, summary: str, prompt: str) -> list[str]:
    """
    Generate images using Grok (xAI). Returns list of image URLs.
    Returns empty list on failure so the blog can still be published without images.
    """
    image_prompt = (
        f"High quality editorial blog header image for: {title}. "
        f"Modern premium style. Context: {summary[:120]}"
    )

    payload = {
        "model": config.model,
        "prompt": image_prompt,
        "n": config.image_count,
    }

    try:
        logger.debug("Grok image request model=%s count=%d", config.model, config.image_count)
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                config.endpoint,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            # xAI returns {"data": [{"url": "...", ...}]}
            urls = [item["url"] for item in body.get("data", []) if item.get("url")]
            logger.debug("Grok returned %d image URL(s)", len(urls))
            return urls
    except Exception as exc:
        logger.warning("Image generation failed (continuing without images): %s", exc)
        return []
