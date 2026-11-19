"""Use what people really search for (Google Search Console) to improve the blog.

Two uses, both set in rules/search_demand.json (the code has no values of its own):

* **Refresh** — a blog post that Google already shows at positions `position_from`
  to `position_to` for a real phrase gets a search title and description written
  for that phrase, and a short section that answers it. A few posts per run, the
  old version kept (`seo_refreshes` table), and a post is not touched again until
  `wait_days_before_touching_again` have passed.
* **New posts** — real phrases with no page inside `no_page_within_position` are
  handed to the blog title generator as the searches to write for.

Search Console also reports machine-made phrases ("genealogy equipment for light
sleepers"). A phrase only counts when it passes the word-count and ignore rules
and, with `ask_model_if_real`, the model agrees a person would type it to find
that page. Its verdict is remembered for `remember_days`.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

import db
import providers
import shopify_client
from config import StoreConfig

from .search_console import collect_search_console

logger = logging.getLogger("ai_blog_server.seo_growth")

RULES_FILE = Path(__file__).resolve().parents[2] / "rules" / "search_demand.json"
RULES_FILE_ENV = "SEO_SEARCH_DEMAND_RULES_FILE"     # tests point this at their own copy
LAST_RUN_KEY = "search_demand_last_run"
VERDICTS_KEY = "search_demand_phrase_verdicts"
ANSWER_CLASS = "bll-answer"
_ANSWER_BLOCK = re.compile(rf'<div class="{ANSWER_CLASS}"[^>]*>.*?</div>', re.S)
_BLOG_POST = re.compile(r"^/blogs/([^/]+)/([^/]+)$")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhraseRules(_Strict):
    min_words: int = Field(ge=1, le=20)
    max_words: int = Field(ge=1, le=30)
    ignore_containing: list[str]
    ask_model_if_real: bool
    model_batch_size: int = Field(ge=5, le=200)
    remember_days: int = Field(ge=1, le=365)


class RefreshRules(_Strict):
    enabled: bool
    position_from: float = Field(ge=1, le=100)
    position_to: float = Field(ge=1, le=100)
    min_impressions: int = Field(ge=1)
    posts_per_run: int = Field(ge=1, le=50)
    model_attempts: int = Field(ge=1, le=10)     # tries per post: a refused text or a model that did not answer
    wait_days_before_touching_again: int = Field(ge=1, le=365)
    seo_title_max_chars: int = Field(ge=20, le=120)
    meta_description_max_chars: int = Field(ge=50, le=320)
    answer_min_words: int = Field(ge=10, le=500)
    answer_max_words: int = Field(ge=10, le=500)
    article_chars_for_model: int = Field(ge=500, le=60000)

    @model_validator(mode="after")
    def _ordered(self) -> "RefreshRules":
        if self.position_from > self.position_to or self.answer_min_words > self.answer_max_words:
            raise ValueError("refresh needs position_from <= position_to and answer_min_words <= answer_max_words")
        return self


class NewPostRules(_Strict):
    enabled: bool
    no_page_within_position: float = Field(ge=1, le=100)
    min_impressions: int = Field(ge=1)
    same_topic_word_overlap: float = Field(gt=0, le=1)   # share of words two phrases share to count as one topic
    max_phrases_in_prompt: int = Field(ge=1, le=100)


class ModelRules(_Strict):
    # "local": the Mac's shared local model (~/.config/local-ai/model.json). "store_setting": the model from the
    # Models page that the store setting in `store_setting` names. The first that answers is used.
    try_in_order: list[Literal["local", "store_setting"]] = Field(min_length=1)
    store_setting: str = Field(min_length=1)
    local_max_tokens: int = Field(ge=200, le=8000)


class SearchDemandRules(_Strict):
    about: str = ""
    enabled: bool
    period_days: int = Field(ge=28, le=365)
    run_every_days: int = Field(ge=1, le=90)
    models: ModelRules
    phrases: PhraseRules
    refresh: RefreshRules
    new_posts: NewPostRules


_CACHE: tuple[str, float, SearchDemandRules] | None = None


def load_rules() -> SearchDemandRules:
    """The rules file, re-read whenever it changes on disk."""
    global _CACHE
    path = Path(os.environ.get(RULES_FILE_ENV) or RULES_FILE).expanduser()
    stamp = path.stat().st_mtime
    if _CACHE is None or _CACHE[0] != str(path) or _CACHE[1] != stamp:
        _CACHE = (str(path), stamp, SearchDemandRules.model_validate(json.loads(path.read_text())))
    return _CACHE[2]


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------

@dataclass
class Phrase:
    text: str
    impressions: int = 0
    clicks: int = 0
    page: str = ""              # path of the page Google shows most for it
    position: float = 0.0       # that page's average position
    best_position: float = 999.0


def _path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def summarise(rows: list[dict[str, Any]]) -> list[Phrase]:
    """One entry per phrase from Search Console's (query, page) rows, most shown first."""
    by_phrase: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for row in rows:
        phrase = " ".join(str(row.get("query") or "").lower().split())
        page = _path(str(row.get("page") or ""))
        if not phrase or not page:
            continue
        slot = by_phrase[phrase].setdefault(page, {"impressions": 0.0, "clicks": 0.0, "weighted": 0.0})
        impressions = float(row.get("impressions") or 0)
        slot["impressions"] += impressions
        slot["clicks"] += float(row.get("clicks") or 0)
        slot["weighted"] += float(row.get("position") or 0) * impressions
    phrases: list[Phrase] = []
    for text, pages in by_phrase.items():
        positions = {page: (p["weighted"] / p["impressions"] if p["impressions"] else 999.0) for page, p in pages.items()}
        main = max(pages, key=lambda page: pages[page]["impressions"])
        phrases.append(Phrase(
            text=text, impressions=int(sum(p["impressions"] for p in pages.values())),
            clicks=int(sum(p["clicks"] for p in pages.values())), page=main,
            position=round(positions[main], 1), best_position=round(min(positions.values()), 1),
        ))
    return sorted(phrases, key=lambda p: (-p.impressions, p.text))


def passes_word_rules(phrase: str, rules: PhraseRules) -> bool:
    words = phrase.split()
    if not rules.min_words <= len(words) <= rules.max_words:
        return False
    return not any(part.lower() in phrase for part in rules.ignore_containing)


class _LocalModel:
    """The Mac's shared local model, asked through Social Autopilot's client so the two take turns on it."""

    def __init__(self, store_id: str, max_tokens: int) -> None:
        self.store_id, self.max_tokens = store_id, max_tokens

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        from services.social_engine.ai.base import LLMRequest
        from services.social_engine.ai.llm import primary_provider
        from services.social_engine.errors import SocialEngineError
        from services.social_engine.http import CallContext
        from services.social_engine.settings import load_settings

        ai = (await load_settings(self.store_id)).ai
        request = LLMRequest(prompt=prompt, system=system_prompt, json_mode=True,
                             temperature=ai.decision_temperature, max_tokens=self.max_tokens)
        try:
            reply = await primary_provider(ai).complete(
                request, CallContext(store_id=self.store_id), operation="search_demand")
        except SocialEngineError as exc:
            raise providers.ProviderError(f"the local model did not answer: {exc}") from exc
        return reply.text


class _FirstToAnswer:
    """The models in the order of the rules: the next one is asked only when the one before gave no answer."""

    def __init__(self, models: list[tuple[str, Any]]) -> None:
        self.models = models

    async def generate_raw(self, prompt: str, system_prompt: str = "") -> str:
        for position, (name, model) in enumerate(self.models):
            try:
                return await model.generate_raw(prompt, system_prompt)
            except (httpx.HTTPError, providers.ProviderError) as exc:
                if position == len(self.models) - 1:
                    raise
                logger.warning("Search demand: %s gave no answer (%s); asking %s.",
                               name, f"{type(exc).__name__}: {exc}".rstrip(": "), self.models[position + 1][0])
        raise RuntimeError("No model to ask.")


async def _model(store_id: str, rules: SearchDemandRules):
    models: list[tuple[str, Any]] = []
    for source in dict.fromkeys(rules.models.try_in_order):
        if source == "local":
            models.append(("the local model", _LocalModel(store_id, rules.models.local_max_tokens)))
            continue
        model_id = await db.get_store_setting(store_id, rules.models.store_setting, "")
        row = await db.get_model(model_id) if model_id else None
        if row and row.get("store_id") == store_id:
            models.append((row.get("name") or model_id, providers.get_text_provider(providers.ModelRecord.from_dict(row))))
    if not models:
        raise RuntimeError(f"No model is set for search demand (store setting '{rules.models.store_setting}').")
    return _FirstToAnswer(models)


def _json_object(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object in the model's answer: {text[:200]}")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("The model's answer is not a JSON object.")
    return data


_REAL_SYSTEM = (
    "You judge search phrases reported by Google Search Console for a wellness shop's blog. Some were typed by "
    "people; others are machine-made filler (an unrelated word dropped into a template, nonsense comparisons). "
    "Reply with one JSON object only."
)


async def real_phrases(store_id: str, phrases: list[Phrase], titles: dict[str, str], rules: SearchDemandRules) -> list[Phrase]:
    """The phrases a person would really type, by the rules and (when switched on) the model's remembered verdict."""
    candidates = [p for p in phrases if passes_word_rules(p.text, rules.phrases)]
    if not rules.phrases.ask_model_if_real or not candidates:
        return candidates
    now = int(time.time())
    keep_after = now - rules.phrases.remember_days * 86400
    try:
        saved = json.loads(await db.get_store_setting(store_id, VERDICTS_KEY, "") or "{}")
    except json.JSONDecodeError:
        saved = {}
    verdicts: dict[str, list] = {k: v for k, v in saved.items() if isinstance(v, list) and len(v) == 2 and v[1] >= keep_after}
    unknown = [p for p in candidates if p.text not in verdicts]
    if unknown:
        provider = await _model(store_id, rules)
        size = rules.phrases.model_batch_size
        for start in range(0, len(unknown), size):
            batch = unknown[start:start + size]
            listing = "\n".join(
                f"{i + 1}. {p.text}  (shown with: {titles.get(p.page) or p.page})" for i, p in enumerate(batch)
            )
            prompt = (
                "For each numbered phrase, decide whether a real person would type it into Google and be well "
                "served by the page it is shown with. Reject phrases with an unrelated word forced in "
                '(e.g. "genealogy equipment for light sleepers"), nonsense comparisons, and phrases about '
                "something the page is not about.\n\n"
                f"{listing}\n\n"
                'Reply with: {"real": [numbers of the real phrases]}'
            )
            answer = _json_object(await provider.generate_raw(prompt, _REAL_SYSTEM))
            chosen = {int(n) for n in answer.get("real") or [] if str(n).strip().lstrip("-").isdigit()}
            for i, phrase in enumerate(batch):
                verdicts[phrase.text] = [(i + 1) in chosen, now]
        await db.set_store_settings(store_id, {VERDICTS_KEY: json.dumps(verdicts, ensure_ascii=False)})
    return [p for p in candidates if verdicts.get(p.text, [False])[0]]


# ---------------------------------------------------------------------------
# What to do with them
# ---------------------------------------------------------------------------

@dataclass
class RefreshPlan:
    path: str
    phrase: Phrase                                   # the phrase the post is refreshed for
    also: list[Phrase] = field(default_factory=list)  # other real phrases the same post nearly ranks for


def plan_refreshes(phrases: list[Phrase], rules: RefreshRules, *, recently_touched: set[str]) -> list[RefreshPlan]:
    """Blog posts worth refreshing, most searched first (English posts only; the French copy follows the English)."""
    by_page: dict[str, list[Phrase]] = defaultdict(list)
    for phrase in phrases:
        if not _BLOG_POST.match(phrase.page) or phrase.page in recently_touched:
            continue
        if rules.position_from <= phrase.position <= rules.position_to:
            by_page[phrase.page].append(phrase)
    plans = []
    for page, items in by_page.items():
        items.sort(key=lambda p: (-p.impressions, p.text))
        if items[0].impressions >= rules.min_impressions:
            plans.append(RefreshPlan(path=page, phrase=items[0], also=items[1:6]))
    plans.sort(key=lambda plan: (-sum(p.impressions for p in [plan.phrase, *plan.also]), plan.path))
    return plans


def _topic_words(phrase: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower().replace("-", " "))
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def _same_topic(a: frozenset[str], b: frozenset[str], overlap: float) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= overlap


def uncovered(phrases: list[Phrase], rules: NewPostRules, *, known: list[Phrase] | None = None) -> list[Phrase]:
    """Real phrases the shop has no page near the first two result pages for: one per topic, and none whose
    topic already has a page there under other wording (`known` = every phrase Search Console reported)."""
    covered = [_topic_words(p.text) for p in (known or phrases) if p.best_position <= rules.no_page_within_position]
    found: list[Phrase] = []
    taken: list[frozenset[str]] = []
    for phrase in phrases:
        if phrase.best_position <= rules.no_page_within_position or phrase.impressions < rules.min_impressions:
            continue
        words = _topic_words(phrase.text)
        if any(_same_topic(words, other, rules.same_topic_word_overlap) for other in covered + taken):
            continue
        found.append(phrase)
        taken.append(words)
    return found[:rules.max_phrases_in_prompt]


# ---------------------------------------------------------------------------
# Refreshing one post
# ---------------------------------------------------------------------------

_ARTICLE = """query($q:String!){ articles(first:5, query:$q){ nodes{
  id handle title body isPublished blog{handle}
  titleTag: metafield(namespace:"global", key:"title_tag"){value}
  descriptionTag: metafield(namespace:"global", key:"description_tag"){value} } } }"""
_UPDATE = """mutation($id:ID!, $article:ArticleUpdateInput!){ articleUpdate(id:$id, article:$article){
  article{id} userErrors{field message} } }"""
_DELETE_METAFIELDS = """mutation($m:[MetafieldIdentifierInput!]!){ metafieldsDelete(metafields:$m){ userErrors{field message} } }"""


async def _article(store: StoreConfig, path: str) -> dict[str, Any] | None:
    match = _BLOG_POST.match(path)
    if not match:
        return None
    blog, handle = match.groups()
    data = await shopify_client.graphql_request(store, _ARTICLE, {"q": f"handle:{handle}"})
    for node in (data.get("articles") or {}).get("nodes") or []:
        if node["handle"] == handle and node["blog"]["handle"] == blog and node["isPublished"]:
            return node
    return None


def _plain(body: str, limit: int) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", body or "", flags=re.S | re.I)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    return text[:limit]


def checked_refresh(answer: dict[str, Any], phrase: str, rules: RefreshRules, *, health_checks: bool = True) -> dict[str, str]:
    """The model's answer as the four texts to write, or ValueError saying why it may not be used."""
    out = {key: " ".join(str(answer.get(key) or "").split()) for key in ("seo_title", "meta_description", "question", "answer")}
    missing = [key for key, value in out.items() if not value]
    if missing:
        raise ValueError(f"missing: {', '.join(missing)}")
    if len(out["seo_title"]) > rules.seo_title_max_chars:
        raise ValueError(f"the search title is {len(out['seo_title'])} characters; the limit is {rules.seo_title_max_chars}")
    if len(out["meta_description"]) > rules.meta_description_max_chars:
        raise ValueError(f"the description is {len(out['meta_description'])} characters; the limit is {rules.meta_description_max_chars}")
    words = len(out["answer"].split())
    if not rules.answer_min_words <= words <= rules.answer_max_words:
        raise ValueError(f"the answer is {words} words; it must be {rules.answer_min_words}-{rules.answer_max_words}")
    if any("<" in value or ">" in value for value in out.values()):
        raise ValueError("the texts must be plain text without HTML")
    from services.social_engine.ai.guard import claim_issues

    blocked = [issue.message for issue in claim_issues(" ".join(out.values()), "", "the refreshed text", health=health_checks)
               if issue.severity == "block"]
    if blocked:
        raise ValueError("; ".join(blocked[:3]))
    return out


def with_answer(body: str, question: str, answer: str, phrase: str) -> str:
    """`body` with the answer section right after its first paragraph (replacing an earlier one)."""
    block = (f'<div class="{ANSWER_CLASS}" data-phrase="{html.escape(phrase, quote=True)}">'
             f"<h2>{html.escape(question)}</h2><p>{html.escape(answer)}</p></div>")
    body = _ANSWER_BLOCK.sub("", body or "")
    end = body.find("</p>")
    return body[:end + 4] + block + body[end + 4:] if end != -1 else block + body


_REFRESH_SYSTEM = (
    "You are the SEO editor of a wellness shop's blog. You adapt an existing article to the phrase people "
    "actually search for, using only what the article already says. Reply with one JSON object only."
)


async def refresh_article(store_id: str, store: StoreConfig, plan: RefreshPlan, rules: SearchDemandRules, *,
                          provider=None) -> dict[str, Any]:
    """Refresh one post for its phrase. Returns {"status": "applied" | "skipped", ...}; never raises for one post."""
    article = await _article(store, plan.path)
    if not article:
        return {"status": "skipped", "path": plan.path, "reason": "the post was not found or is not published"}
    provider = provider or await _model(store_id, rules)
    r = rules.refresh
    prompt = "\n".join([
        f"Search phrase people use: {plan.phrase.text}",
        f"Google shows this article for it at position {plan.phrase.position}, {plan.phrase.impressions} times in "
        f"{rules.period_days} days, with {plan.phrase.clicks} clicks.",
        *([f"Related phrases it is also shown for: {'; '.join(p.text for p in plan.also)}"] if plan.also else []),
        f"Article headline: {article['title']}",
        f"Current search title: {(article.get('titleTag') or {}).get('value') or article['title']}",
        f"Current search description: {(article.get('descriptionTag') or {}).get('value') or '(none set)'}",
        "",
        "Article text:",
        _plain(article["body"], r.article_chars_for_model),
        "",
        "Write:",
        f'- "seo_title": the title shown in Google, at most {r.seo_title_max_chars} characters, containing the '
        "search phrase in natural wording, honest about what the article gives.",
        f'- "meta_description": the description shown in Google, at most {r.meta_description_max_chars} characters, '
        "saying plainly what the reader gets, with a reason to click.",
        '- "question": the search phrase as the question a reader has (a section heading).',
        f'- "answer": a direct answer to that question in {r.answer_min_words}-{r.answer_max_words} words of plain '
        "text, using only facts and advice that are already in the article.",
        "",
        "Rules: no new facts, numbers, prices or product names. No medical claims: nothing cures, treats or "
        "prevents a condition. No year unless the article is about that year. British English. No HTML.",
        "",
        'Reply with: {"seo_title": "...", "meta_description": "...", "question": "...", "answer": "..."}',
    ])
    texts, problem, silence = None, "", ""
    for _ in range(r.model_attempts):
        ask = prompt + (f"\n\nYour last answer was refused: {problem}. Fix that." if problem else "")
        try:
            texts = checked_refresh(_json_object(await provider.generate_raw(ask, _REFRESH_SYSTEM)), plan.phrase.text, r)
            break
        except (ValueError, json.JSONDecodeError) as exc:      # an answer that may not be used: say why, ask again
            problem = str(exc)
        except (httpx.HTTPError, providers.ProviderError) as exc:   # no answer at all: ask again as before
            silence = f"{type(exc).__name__}: {exc}".rstrip(": ")
            logger.warning("Search demand: the model did not answer for %s: %s", plan.path, silence)
    if texts is None:
        reason = problem or silence or "the model did not answer"
        return {"status": "skipped", "path": plan.path, "reason": f"no usable text from the model ({reason[:200]})"}

    before = {
        "title_tag": (article.get("titleTag") or {}).get("value"),
        "description_tag": (article.get("descriptionTag") or {}).get("value"),
        "body": article["body"],
    }
    new_body = with_answer(article["body"], texts["question"], texts["answer"], plan.phrase.text)
    result = await shopify_client.graphql_request(store, _UPDATE, {"id": article["id"], "article": {
        "body": new_body,
        "metafields": [
            {"namespace": "global", "key": "title_tag", "type": "single_line_text_field", "value": texts["seo_title"]},
            {"namespace": "global", "key": "description_tag", "type": "single_line_text_field", "value": texts["meta_description"]},
        ],
    }})
    errors = (result.get("articleUpdate") or {}).get("userErrors") or []
    if errors:
        return {"status": "skipped", "path": plan.path, "reason": f"Shopify refused the change: {errors}"}
    refresh_id = await db.record_seo_refresh(
        store_id, article_gid=article["id"], path=plan.path, phrase=plan.phrase.text,
        impressions=plan.phrase.impressions, position=plan.phrase.position, before=before,
        after={"title_tag": texts["seo_title"], "description_tag": texts["meta_description"],
               "question": texts["question"], "answer": texts["answer"]},
    )
    logger.info("Search demand: refreshed %s for '%s' (position %.1f, %d impressions)", plan.path, plan.phrase.text,
                plan.phrase.position, plan.phrase.impressions,
                extra={"operation": "search_demand_refresh", "store_id": store_id, "correlation_id": refresh_id})
    return {"status": "applied", "id": refresh_id, "path": plan.path, "phrase": plan.phrase.text, **texts}


async def restore_refresh(store_id: str, refresh_id: str) -> dict[str, Any]:
    """Put a refreshed post back exactly as it was."""
    entry = await db.get_seo_refresh(store_id, refresh_id)
    if not entry or entry["status"] != "applied":
        return {"restored": False, "reason": "no applied refresh with that id"}
    store = StoreConfig.from_row(await db.get_store(store_id))
    before = entry["before"]
    keep = [{"namespace": "global", "key": key, "type": "single_line_text_field", "value": before[key]}
            for key in ("title_tag", "description_tag") if before.get(key)]
    result = await shopify_client.graphql_request(store, _UPDATE, {"id": entry["article_gid"], "article": {
        "body": before["body"], **({"metafields": keep} if keep else {}),
    }})
    errors = (result.get("articleUpdate") or {}).get("userErrors") or []
    if errors:
        return {"restored": False, "reason": f"Shopify refused: {errors}"}
    drop = [{"ownerId": entry["article_gid"], "namespace": "global", "key": key}
            for key in ("title_tag", "description_tag") if not before.get(key)]
    if drop:
        await shopify_client.graphql_request(store, _DELETE_METAFIELDS, {"m": drop})
    await db.mark_seo_refresh_restored(refresh_id)
    return {"restored": True, "path": entry["path"]}


# ---------------------------------------------------------------------------
# The whole cycle
# ---------------------------------------------------------------------------

async def _search_rows(store_id: str, rules: SearchDemandRules) -> list[dict[str, Any]] | None:
    site = await db.get_store_setting(store_id, "seo_gsc_site_url", "")
    credentials = await db.get_store_setting(store_id, "seo_gsc_service_account_json", "")
    if not credentials and await db.get_store_setting(store_id, "seo_use_ga4_credentials", "") == "1":
        credentials = await db.get_store_setting(store_id, "ga4_service_account_json", "")
    if not site or not credentials:
        return None
    return (await collect_search_console(site, credentials, rules.period_days))["current"]


async def _titles(store: StoreConfig, pages: set[str]) -> dict[str, str]:
    """Headlines of the blog posts among `pages` (helps the model judge a phrase against its page)."""
    titles: dict[str, str] = {}
    for page in sorted(pages):
        article = await _article(store, page)
        if article:
            titles[page] = article["title"]
    return titles


async def demand(store_id: str, *, rules: SearchDemandRules | None = None) -> dict[str, Any] | None:
    """Real phrases and what they call for; None when Search Console is not connected for the store."""
    rules = rules or load_rules()
    rows = await _search_rows(store_id, rules)
    if rows is None:
        return None
    store = StoreConfig.from_row(await db.get_store(store_id))
    phrases = summarise(rows)
    worth_judging = [p for p in phrases if passes_word_rules(p.text, rules.phrases)
                     and p.impressions >= min(rules.refresh.min_impressions, rules.new_posts.min_impressions)]
    titles = await _titles(store, {p.page for p in worth_judging if _BLOG_POST.match(p.page)})
    real = await real_phrases(store_id, worth_judging, titles, rules)
    since = int(time.time()) - rules.refresh.wait_days_before_touching_again * 86400
    touched = {entry["path"] for entry in await db.list_seo_refreshes(store_id, since=since, limit=2000)
               if entry["status"] == "applied"}
    return {
        "phrases": len(phrases), "judged": len(worth_judging), "real": real,
        "refresh": plan_refreshes(real, rules.refresh, recently_touched=touched) if rules.refresh.enabled else [],
        "uncovered": uncovered(real, rules.new_posts, known=phrases) if rules.new_posts.enabled else [],
    }


async def run_cycle(store_id: str, *, apply: bool = True) -> dict[str, Any]:
    """Find the real phrases and refresh up to `posts_per_run` posts. `apply=False` only reports the plan."""
    rules = load_rules()
    if not rules.enabled:
        return {"status": "off"}
    found = await demand(store_id, rules=rules)
    if found is None:
        return {"status": "not_connected", "message": "Search Console is not connected for this store."}
    plans: list[RefreshPlan] = found["refresh"][:rules.refresh.posts_per_run]
    summary: dict[str, Any] = {
        "status": "planned" if not apply else "done",
        "phrases_seen": found["phrases"], "real_phrases": len(found["real"]),
        "refresh_candidates": len(found["refresh"]),
        "plan": [{"path": p.path, "phrase": p.phrase.text, "impressions": p.phrase.impressions,
                  "position": p.phrase.position} for p in plans],
        "new_post_phrases": [{"phrase": p.text, "impressions": p.impressions, "best_position": p.best_position}
                             for p in found["uncovered"]],
    }
    if apply:
        store = StoreConfig.from_row(await db.get_store(store_id))
        provider = await _model(store_id, rules) if plans else None
        results = []
        for plan in plans:
            try:
                results.append(await refresh_article(store_id, store, plan, rules, provider=provider))
            except Exception as exc:    # one post never stops the others
                logger.warning("Search demand: could not refresh %s: %s: %s", plan.path, type(exc).__name__, exc)
                results.append({"status": "skipped", "path": plan.path, "reason": f"{type(exc).__name__}: {exc}"})
        summary["results"] = results
        await db.set_store_settings(store_id, {LAST_RUN_KEY: str(int(time.time()))})
    return summary


async def run_scheduled() -> None:
    """Called by the scheduler: run the cycle for each connected store once every `run_every_days`."""
    try:
        rules = load_rules()
    except Exception as exc:
        logger.error("Search demand rules could not be read: %s", exc)
        return
    if not rules.enabled:
        return
    now = int(time.time())
    for store in await db.get_stores():
        store_id = str(store["id"])
        if not await db.get_store_setting(store_id, "seo_gsc_site_url", ""):
            continue
        last = int(await db.get_store_setting(store_id, LAST_RUN_KEY, "") or 0)
        if now - last < rules.run_every_days * 86400:
            continue
        try:
            summary = await run_cycle(store_id)
            logger.info("Search demand cycle for %s: %s", store_id, json.dumps(summary, ensure_ascii=False)[:1500])
        except Exception as exc:
            logger.error("Search demand cycle failed for %s: %s: %s", store_id, type(exc).__name__, exc)
            await db.set_store_settings(store_id, {LAST_RUN_KEY: str(now)})    # try again next period, not every tick


async def title_prompt_addition(store_id: str) -> str:
    """Text added to the blog title generator's prompt: the real searches that have no good page yet."""
    try:
        rules = load_rules()
        if not rules.enabled or not rules.new_posts.enabled:
            return ""
        found = await demand(store_id, rules=rules)
    except Exception as exc:
        logger.warning("Search demand was not added to the title prompt: %s: %s", type(exc).__name__, exc)
        return ""
    if not found or not found["uncovered"]:
        return ""
    lines = "\n".join(f"- {p.text} ({p.impressions} searches shown, best position {p.best_position})" for p in found["uncovered"])
    return (
        "\n\nReal searches from Google Search Console for which this shop has no page on the first two result "
        f"pages (last {rules.period_days} days):\n{lines}\n"
        "Write titles that answer these searches first, one title per search, and use the search phrase word for "
        "word as that title's keyword. Fill the rest of the batch as instructed above."
    )


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Search demand: refresh posts for real search phrases.")
    parser.add_argument("--store", default="store_1")
    parser.add_argument("--apply", action="store_true", help="make the changes (without it, only the plan is shown)")
    parser.add_argument("--log", action="store_true", help="show what was refreshed")
    parser.add_argument("--restore", default="", help="id of a refresh to undo")
    args = parser.parse_args()

    async def _main() -> None:
        await db.init_db()
        if args.restore:
            out: Any = await restore_refresh(args.store, args.restore)
        elif args.log:
            out = [{k: v for k, v in entry.items() if k != "before"} for entry in await db.list_seo_refreshes(args.store)]
        else:
            out = await run_cycle(args.store, apply=args.apply)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    asyncio.run(_main())
