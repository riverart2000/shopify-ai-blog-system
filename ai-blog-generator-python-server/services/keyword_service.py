"""services/keyword_service.py — Search-intent keyword discovery via Tavily, Exa & Google.

Strategy:
  - Tavily: targets Reddit + Quora with "advanced" depth. Real discussion post titles
    from these communities are de-facto long-tail search queries.
  - Exa: neural search scoped to reddit.com, last 30 days only — very fresh signal.
  - Google Suggest: calls Google's public autocomplete endpoint (no API key required)
    to see what people are currently typing into Google for the niche.
  All three run concurrently. Results are stored in the pool with their
  content snippets so the LLM has real discussion context when writing the blog.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

import db
from utils import log_debug_payload

logger = logging.getLogger("ai_blog_server")

# ── Normalisation helpers ────────────────────────────────────────────────────
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_LISTICLE_RE = re.compile(r"^\s*\d+[\s.)-]+")   # "10 Best …", "1. How to …"
_NOISE_CHARS_RE = re.compile(r"[^a-z0-9\s',-]")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_MIN_WORDS = 3
_MAX_WORDS = 15  # Reddit titles can be longer natural-language queries


def _normalise_title(title: str) -> Optional[str]:
    """Convert a search-result title into a clean long-tail keyword phrase, or None."""
    kw = title.lower()
    kw = _YEAR_RE.sub("", kw)
    kw = _LISTICLE_RE.sub("", kw)
    # Take only the first segment (before pipe / em-dash / hyphen separator)
    for sep in ("|", "—", " : ", " - "):
        kw = kw.split(sep)[0]
    kw = _NOISE_CHARS_RE.sub(" ", kw)
    kw = _MULTI_SPACE_RE.sub(" ", kw).strip().strip(",").strip()
    words = kw.split()
    if len(words) < _MIN_WORDS or len(words) > _MAX_WORDS:
        return None
    # Discard mostly single-character tokens
    if sum(1 for w in words if len(w) < 2) > len(words) // 2:
        return None
    return " ".join(words)


# ── API helpers ──────────────────────────────────────────────────────────────

async def _search_tavily(api_key: str, query: str, num_results: int = 2) -> list[dict]:
    """Search Reddit + Quora via Tavily with advanced depth.

    Discussion post titles from these communities are real search intent queries.
    """
    tavily_payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": num_results,
        "include_answer": False,
        "include_domains": ["reddit.com", "quora.com"],
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        log_debug_payload(logger, "Tavily → payload", {**tavily_payload, "api_key": "<redacted>"})
        resp = await client.post(
            "https://api.tavily.com/search",
            json=tavily_payload,
        )
        resp.raise_for_status()
        data = resp.json()
    log_debug_payload(logger, "Tavily ← response", data)
    results = []
    for r in data.get("results", []):
        kw = _normalise_title(r.get("title", ""))
        if kw:
            results.append({"keyword": kw, "content": (r.get("content") or "").strip()})
    return results


async def _search_exa(api_key: str, query: str, num_results: int = 2) -> list[dict]:
    """Search Reddit via Exa neural search, limited to the last 30 days.

    Reddit post titles are typed exactly how people search — high-intent signal.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    exa_payload = {
        "query": query,
        "numResults": num_results,
        "type": "neural",
        "includeDomains": ["reddit.com"],
        "startPublishedDate": cutoff,
        "contents": {"text": {"maxCharacters": 500}},
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        log_debug_payload(logger, "Exa → payload", exa_payload)
        resp = await client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=exa_payload,
        )
        resp.raise_for_status()
        data = resp.json()
    log_debug_payload(logger, "Exa ← response", data)
    results = []
    for r in data.get("results", []):
        kw = _normalise_title(r.get("title", ""))
        if kw:
            content = ""
            if isinstance(r.get("text"), str):
                content = r["text"].strip()
            results.append({"keyword": kw, "content": content})
    return results


# ── Public API ───────────────────────────────────────────────────────────────

async def fetch_keywords(
    store_id: str,
    niche: str,
    max_pool: int = 100,
) -> dict:
    """Fetch long-tail keywords for *niche* from both Tavily and Exa, store all in pool.

    Returns::

        {
          "immediate":         str | None,   # first keyword popped from pool
          "immediate_content": str,           # discussion snippet for LLM context
          "added":             int,           # total rows inserted into pool
          "pool_count":        int,           # total pool size after this call
          "source":            str,           # e.g. "Tavily, Exa"
          "error":             str | None,
        }

    Flow:
      1. Query Tavily and Exa concurrently (2 results each).
      2. Normalise titles → keyword phrases; attach content snippets.
      3. Store all results in the pool (deduped by UNIQUE constraint).
      4. Pop the oldest entry from pool → returned as ``immediate``.
    """
    niche = niche.strip()
    if not niche:
        return {"immediate": None, "immediate_content": "", "added": 0, "pool_count": 0, "source": "", "error": "No niche configured."}

    tavily_key = await db.get_store_setting(store_id, "tavily_api_key", "")
    exa_key = await db.get_store_setting(store_id, "exa_api_key", "")

    if not tavily_key and not exa_key:
        return {
            "immediate": None, "immediate_content": "", "added": 0,
            "pool_count": await db.count_keyword_pool(store_id),
            "source": "", "error": "No Tavily or Exa API key configured.",
        }

    query = niche
    errors: list[str] = []

    # ── Run both APIs concurrently ──────────────────────────────────────────
    async def _safe_tavily() -> tuple[list[dict], str]:
        if not tavily_key:
            return [], ""
        try:
            results = await _search_tavily(tavily_key, query)
            if results:
                logger.info("Tavily returned %d results for store %s", len(results), store_id)
            else:
                logger.info("Tavily returned 0 usable results for store %s", store_id)
            return results, "Tavily" if results else ""
        except httpx.HTTPStatusError as exc:
            msg = f"Tavily error {exc.response.status_code}"
            logger.warning("Tavily search failed for store %s: %s", store_id, msg)
            errors.append(msg)
            return [], ""
        except Exception as exc:
            logger.warning("Tavily search failed for store %s: %s", store_id, exc)
            errors.append(f"Tavily: {exc}")
            return [], ""

    async def _safe_exa() -> tuple[list[dict], str]:
        if not exa_key:
            return [], ""
        try:
            results = await _search_exa(exa_key, query)
            if results:
                logger.info("Exa returned %d results for store %s", len(results), store_id)
            else:
                logger.info("Exa returned 0 usable results for store %s", store_id)
            return results, "Exa" if results else ""
        except httpx.HTTPStatusError as exc:
            msg = f"Exa error {exc.response.status_code}"
            logger.warning("Exa search failed for store %s: %s", store_id, msg)
            errors.append(msg)
            return [], ""
        except Exception as exc:
            logger.warning("Exa search failed for store %s: %s", store_id, exc)
            errors.append(f"Exa: {exc}")
            return [], ""

    (tavily_results, tavily_src), (exa_results, exa_src) = await asyncio.gather(
        _safe_tavily(), _safe_exa()
    )

    # Merge, deduplicate by keyword phrase
    seen: set[str] = set()
    all_results: list[dict] = []
    sources_used: list[str] = []
    for items, src in ((tavily_results, tavily_src), (exa_results, exa_src)):
        for item in items:
            kw = item["keyword"]
            if kw not in seen:
                seen.add(kw)
                all_results.append(item)
        if src and src not in sources_used:
            sources_used.append(src)

    source = ", ".join(sources_used)

    if not all_results:
        return {
            "immediate": None, "immediate_content": "", "added": 0,
            "pool_count": await db.count_keyword_pool(store_id),
            "source": source,
            "error": "; ".join(errors) if errors else "No usable results returned from search APIs.",
        }

    # Store everything; let UNIQUE constraint deduplicate against existing pool entries
    added = await db.add_keywords(store_id, all_results, max_pool=max_pool, source=source)

    # Pop the oldest entry as the immediate keyword
    immediate_row = await db.pop_keyword(store_id)
    immediate = immediate_row["keyword"] if immediate_row else None
    immediate_content = (immediate_row.get("content") or "") if immediate_row else ""
    pool_count = await db.count_keyword_pool(store_id)

    logger.info(
        "Keywords for store %s: immediate=%r, stored=%d, pool=%d, sources=%s",
        store_id, immediate, added, pool_count, source,
    )
    return {
        "immediate": immediate,
        "immediate_content": immediate_content,
        "added": added,
        "pool_count": pool_count,
        "source": source,
        "error": None,
    }

