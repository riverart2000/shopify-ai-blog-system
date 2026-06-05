from __future__ import annotations

from difflib import SequenceMatcher
import datetime
import html as _html
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

import db

_quality_log = logging.getLogger("ai_blog_server.quality")


_WORD_RE = re.compile(r"\b[\w'-]+\b")
_HEADING_RE = re.compile(r"^\s*#{1,3}\s+\S+", re.MULTILINE)
_CTA_RE = re.compile(
    r"\b(shop|buy|browse|discover|explore|compare|view|see|find|order|learn more)\b",
    re.IGNORECASE,
)
_FILLER_PHRASES = [
    "in today's",
    "in todays",
    "in conclusion",
    "it is important to note",
    "whether you are",
    "whether you're",
    "when it comes to",
    "in the world of",
]
_ARTIFACT_RE = re.compile(
    r"(```|\[insert[^\]]*\]|\[placeholder[^\]]*\]|\{\{[^}]+\}\}|lorem ipsum|as an ai|"
    r"i cannot|i can't|tbd|todo|<\/?[a-z][^>]*>)",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+\S+", re.MULTILINE)
_LINK_RE = re.compile(r"(https?://|www\.|\]\(|\b/shop/|\b/products/)", re.IGNORECASE)
_FAQ_RE = re.compile(
    r"\b(faq|frequently asked|questions?|how do|how can|what is|what are|why does|which|when should)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")
_TRUST_CLAIM_RE = re.compile(
    r"\b(cure|cures|cured|treats?|prevents?|diagnose|diagnoses|guaranteed|guarantees|"
    r"clinically proven|fda approved|no side effects|risk[- ]free|miracle|instant results?)\b",
    re.IGNORECASE,
)
_ABSOLUTE_CLAIM_RE = re.compile(
    r"\b(always|never|everyone|no one|100%|completely safe|best ever|the best|perfect for everyone)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "about", "after", "again", "also", "blog", "commerce", "content", "could", "from",
    "generate", "following", "guide", "into", "post", "product", "shopify", "store",
    "their", "there", "these", "thing", "this", "those", "through", "using", "while",
    "with", "write", "your", "youre", "you're", "would",
}


@dataclass
class QualityCheck:
    key: str
    label: str
    status: str
    message: str
    impact: int
    hint: str = ""


@dataclass
class QualityReport:
    score: int
    verdict: str
    verdict_label: str
    publish_blocked: bool
    word_count: int
    heading_count: int
    paragraph_count: int
    image_count: int
    duplicate_similarity: float
    duplicate_title: str
    duplicate_article_url: str
    checks: list[QualityCheck]

    def as_dict(self) -> dict:
        return asdict(self)


class QualityGateError(Exception):
    def __init__(self, report: QualityReport):
        self.report = report
        detail = f"score={report.score} verdict={report.verdict}"
        if report.duplicate_title:
            detail += f" duplicate={report.duplicate_title!r}"
        super().__init__(f"Quality checks blocked publishing ({detail})")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]


def _normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _normalized_list(values: Optional[list[str]]) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            result.append(text)
    return result


def _important_terms(text: str, limit: int = 12) -> list[str]:
    terms: list[str] = []
    for word in _words(_normalize_text(text)):
        if len(word) < 4 or word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
        if len(terms) >= limit:
            break
    return terms


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = f" {_normalize_text(text)} "
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in normalized_text


def _count_phrase(text: str, phrase: str) -> int:
    normalized_text = _normalize_text(text)
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return 0
    return normalized_text.count(normalized_phrase)


def _first_paragraph(content: str) -> str:
    paragraphs = _paragraphs(content)
    return paragraphs[0] if paragraphs else ""


def _heading_lines(content: str) -> list[str]:
    return [line.strip("# ").strip() for line in content.splitlines() if re.match(r"^\s*#{1,3}\s+\S+", line)]


def _sentence_parts(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.findall(text or "") if part.strip()]


def html_to_review_text(html: str) -> str:
    """Convert stored Shopify HTML into plain text/markdown-like review text."""
    text = html or ""
    text = re.sub(
        r"<h[1-6][^>]*>(.*?)</h[1-6]>",
        lambda m: f"\n## {_normalize_inline_html(m.group(1))}\n\n",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</ul>|</ol>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_inline_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = _html.unescape(text)
    return " ".join(text.split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _recompute_verdict(report: QualityReport, *, title: str, summary: str, content: str) -> None:
    report.score = max(0, min(100, report.score))
    report.publish_blocked = (
        report.score < 55
        or report.word_count < 250
        or not title.strip()
        or not summary.strip()
        or not content.strip()
        or report.duplicate_similarity >= 0.88
    )
    if report.publish_blocked:
        report.verdict = "blocked"
        report.verdict_label = "Blocked"
    elif report.score >= 80:
        report.verdict = "ready"
        report.verdict_label = "Ready"
    else:
        report.verdict = "review"
        report.verdict_label = "Needs Review"


def _add_check(
    checks: list[QualityCheck],
    *,
    key: str,
    label: str,
    status: str,
    message: str,
    impact: int = 0,
    hint: str = "",
) -> int:
    checks.append(QualityCheck(key=key, label=label, status=status, message=message, impact=impact, hint=hint))
    return impact


def evaluate_draft(
    *,
    title: str,
    summary: str,
    content: str,
    keywords: Optional[list[str]] = None,
    prompt_text: str = "",
    product_url: str = "",
    product_title: str = "",
    image_count: int = 0,
) -> QualityReport:
    """Return a deterministic local quality report for a draft.

    This is intentionally heuristic rather than model-based. The goal is to
    catch obvious weak drafts before publish without calling any external APIs.
    """
    title = (title or "").strip()
    summary = (summary or "").strip()
    content = (content or "").strip()

    score = 100
    checks: list[QualityCheck] = []

    body_words = len(_words(content))
    summary_len = len(summary)
    title_len = len(title)
    heading_count = len(_HEADING_RE.findall(content))
    paragraph_count = len(_paragraphs(content))
    combined_text = f"{summary}\n{content}".lower()
    filler_hits = [phrase for phrase in _FILLER_PHRASES if phrase in combined_text]
    has_cta = bool(product_url.strip()) or bool(_CTA_RE.search(combined_text))
    keyword_values = _normalized_list(keywords)
    first_paragraph = _first_paragraph(content)
    heading_text = "\n".join(_heading_lines(content))

    _TITLE_HINT = "Target: 45–65 chars (ideal) · 35–80 chars (acceptable)"
    if 45 <= title_len <= 65:
        _add_check(
            checks,
            key="title_length",
            label="Title Length",
            status="pass",
            message=f"{title_len} characters. Good search-result length.",
            hint=_TITLE_HINT,
        )
    elif 35 <= title_len <= 80:
        score -= _add_check(
            checks,
            key="title_length",
            label="Title Length",
            status="warn",
            message=f"{title_len} characters. Aim for roughly 45–65 for cleaner search snippets.",
            impact=6,
            hint=_TITLE_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="title_length",
            label="Title Length",
            status="fail",
            message=f"{title_len} characters. Too short or too long for a strong search title.",
            impact=12,
            hint=_TITLE_HINT,
        )

    _SUMMARY_HINT = "Target: 120–170 chars (ideal) · 80–220 chars (acceptable)"
    if 120 <= summary_len <= 170:
        _add_check(
            checks,
            key="summary_length",
            label="Excerpt Length",
            status="pass",
            message=f"{summary_len} characters. Good excerpt/meta length.",
            hint=_SUMMARY_HINT,
        )
    elif 80 <= summary_len <= 220:
        score -= _add_check(
            checks,
            key="summary_length",
            label="Excerpt Length",
            status="warn",
            message=f"{summary_len} characters. Tighten this toward roughly 120–170 characters.",
            impact=4,
            hint=_SUMMARY_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="summary_length",
            label="Excerpt Length",
            status="fail",
            message=f"{summary_len} characters. Too thin or too long to work well as an excerpt.",
            impact=8,
            hint=_SUMMARY_HINT,
        )

    _WORDS_HINT = "Target: 700+ words (strong) · 450–699 words (acceptable) · <450 words (fail)"
    if body_words >= 700:
        _add_check(
            checks,
            key="word_count",
            label="Content Depth",
            status="pass",
            message=f"{body_words} words. Strong depth for an SEO article.",
            hint=_WORDS_HINT,
        )
    elif body_words >= 450:
        score -= _add_check(
            checks,
            key="word_count",
            label="Content Depth",
            status="warn",
            message=f"{body_words} words. Publishable, but thin for a competitive SEO topic.",
            impact=10,
            hint=_WORDS_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="word_count",
            label="Content Depth",
            status="fail",
            message=f"{body_words} words. Too short for a strong SEO post.",
            impact=22,
            hint=_WORDS_HINT,
        )

    _HEADING_HINT = "Target: 2+ section headings (##) · 1 heading (warn) · 0 headings (fail)"
    if heading_count >= 2:
        _add_check(
            checks,
            key="headings",
            label="Headings",
            status="pass",
            message=f"{heading_count} section headings found.",
            hint=_HEADING_HINT,
        )
    elif heading_count == 1:
        score -= _add_check(
            checks,
            key="headings",
            label="Headings",
            status="warn",
            message="Only one section heading found. Break the post into more scannable sections.",
            impact=8,
            hint=_HEADING_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="headings",
            label="Headings",
            status="fail",
            message="No section headings found. The post needs structure.",
            impact=16,
            hint=_HEADING_HINT,
        )

    _PARA_HINT = "Target: 5+ paragraph blocks (ideal) · 3–4 (acceptable) · <3 (fail)"
    if paragraph_count >= 5:
        _add_check(
            checks,
            key="paragraphs",
            label="Paragraph Structure",
            status="pass",
            message=f"{paragraph_count} paragraph blocks found.",
            hint=_PARA_HINT,
        )
    elif paragraph_count >= 3:
        score -= _add_check(
            checks,
            key="paragraphs",
            label="Paragraph Structure",
            status="warn",
            message=f"{paragraph_count} paragraph blocks found. Add a little more structure.",
            impact=6,
            hint=_PARA_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="paragraphs",
            label="Paragraph Structure",
            status="fail",
            message=f"Only {paragraph_count} paragraph blocks found. The draft is too compressed.",
            impact=12,
            hint=_PARA_HINT,
        )

    _IMAGE_HINT = "Target: 3-4 images of different types attached"
    if image_count >= 3:
        _add_check(
            checks,
            key="images",
            label="Images",
            status="pass",
            message=f"{image_count} images ready for publish.",
            hint=_IMAGE_HINT,
        )
    elif image_count >= 1:
        score -= _add_check(
            checks,
            key="images",
            label="Images",
            status="warn",
            message=(
                f"Only {image_count} image{'s' if image_count != 1 else ''} attached. "
                "Aim for 3-4 of different types for stronger sharing and CTR."
            ),
            impact=3,
            hint=_IMAGE_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="images",
            label="Images",
            status="warn",
            message="No image is attached. The post can still publish, but it is weaker for CTR.",
            impact=5,
            hint=_IMAGE_HINT,
        )

    _CTA_HINT = "Target: at least one CTA verb (shop, buy, browse, explore, discover, order, etc.)"
    if has_cta:
        _add_check(
            checks,
            key="cta",
            label="Commerce CTA",
            status="pass",
            message="A store-facing CTA is present or will be auto-added on publish.",
            hint=_CTA_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="cta",
            label="Commerce CTA",
            status="warn",
            message="No clear shopping CTA detected. Add a line that drives the reader to the store.",
            impact=10,
            hint=_CTA_HINT,
        )

    _FILLER_HINT = "Target: 0 filler phrases · 1–2 (warn) · 3+ (fail)"
    if not filler_hits:
        _add_check(
            checks,
            key="filler",
            label="Generic Filler",
            status="pass",
            message="No common filler phrases detected.",
            hint=_FILLER_HINT,
        )
    elif len(filler_hits) <= 2:
        score -= _add_check(
            checks,
            key="filler",
            label="Generic Filler",
            status="warn",
            message=f"Generic phrasing detected: {', '.join(filler_hits)}.",
            impact=8,
            hint=_FILLER_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="filler",
            label="Generic Filler",
            status="fail",
            message=f"Too much generic phrasing detected: {', '.join(filler_hits)}.",
            impact=14,
            hint=_FILLER_HINT,
        )

    _KW_HINT = "Target: keywords in title + body + 3 of 5 locations (title, summary, first para, heading, body)"
    if keyword_values:
        title_hits = [kw for kw in keyword_values if _contains_phrase(title, kw)]
        summary_hits = [kw for kw in keyword_values if _contains_phrase(summary, kw)]
        first_para_hits = [kw for kw in keyword_values if _contains_phrase(first_paragraph, kw)]
        heading_hits = [kw for kw in keyword_values if _contains_phrase(heading_text, kw)]
        body_hits = [kw for kw in keyword_values if _contains_phrase(content, kw)]
        coverage_count = len(set(title_hits + summary_hits + first_para_hits + heading_hits + body_hits))
        needed = min(3, len(keyword_values))
        if title_hits and body_hits and coverage_count >= needed:
            _add_check(
                checks,
                key="keyword_coverage",
                label="Keyword Coverage",
                status="pass",
                message=f"{coverage_count}/{len(keyword_values)} SEO keywords appear in useful places.",
                hint=_KW_HINT,
            )
        elif body_hits:
            score -= _add_check(
                checks,
                key="keyword_coverage",
                label="Keyword Coverage",
                status="warn",
                message="Keywords appear in the body, but add one naturally to the title, excerpt, or a heading.",
                impact=6,
                hint=_KW_HINT,
            )
        else:
            score -= _add_check(
                checks,
                key="keyword_coverage",
                label="Keyword Coverage",
                status="fail",
                message="Generated SEO keywords are not reflected in the article body.",
                impact=12,
                hint=_KW_HINT,
            )

        _KW_STUFF_HINT = "Target: any single keyword used <8 times in the body"
        repeated_keywords = [kw for kw in keyword_values if body_words and _count_phrase(content, kw) >= 8]
        if repeated_keywords:
            score -= _add_check(
                checks,
                key="keyword_stuffing",
                label="Keyword Stuffing",
                status="warn",
                message=f"Potential overuse detected: {', '.join(repeated_keywords[:3])}.",
                impact=7,
                hint=_KW_STUFF_HINT,
            )
        else:
            _add_check(
                checks,
                key="keyword_stuffing",
                label="Keyword Stuffing",
                status="pass",
                message="No obvious keyword overuse detected.",
                hint=_KW_STUFF_HINT,
            )
    else:
        score -= _add_check(
            checks,
            key="keyword_coverage",
            label="Keyword Coverage",
            status="warn",
            message="No SEO keywords were provided with this draft.",
            impact=4,
            hint=_KW_HINT,
        )

    _RELEVANCE_HINT = "Target: ≥50% of key prompt terms appear in the article · 25–49% (warn) · <25% (fail)"
    prompt_terms = _important_terms(prompt_text)
    if prompt_terms:
        normalized_combined = _normalize_text(f"{title}\n{summary}\n{content}")
        prompt_hits = [term for term in prompt_terms if f" {term} " in f" {normalized_combined} "]
        hit_ratio = len(prompt_hits) / max(1, len(prompt_terms))
        if hit_ratio >= 0.50:
            _add_check(
                checks,
                key="prompt_relevance",
                label="Prompt Relevance",
                status="pass",
                message=f"Covers {len(prompt_hits)}/{len(prompt_terms)} important prompt terms.",
                hint=_RELEVANCE_HINT,
            )
        elif hit_ratio >= 0.25:
            score -= _add_check(
                checks,
                key="prompt_relevance",
                label="Prompt Relevance",
                status="warn",
                message="The article only partially reflects the source prompt. Tighten the angle.",
                impact=8,
                hint=_RELEVANCE_HINT,
            )
        else:
            score -= _add_check(
                checks,
                key="prompt_relevance",
                label="Prompt Relevance",
                status="fail",
                message="The article appears weakly related to the requested prompt.",
                impact=14,
                hint=_RELEVANCE_HINT,
            )
    else:
        _add_check(
            checks,
            key="prompt_relevance",
            label="Prompt Relevance",
            status="pass",
            message="No source prompt context was available for relevance scoring.",
            hint=_RELEVANCE_HINT,
        )

    if product_url.strip():
        _PRODUCT_FIT_HINT = "Target: 3+ product name terms in title/summary/body"
        _PRODUCT_CTA_HINT = "Target: product-specific CTA (shop/buy/explore) in the final ~700 chars"
        product_terms = _important_terms(product_title, limit=8)
        product_hits = [term for term in product_terms if _contains_phrase(f"{title}\n{summary}\n{content}", term)]
        ending_has_cta = bool(_CTA_RE.search(content[-700:].lower())) or bool(product_url.strip())
        if product_terms and len(product_hits) >= max(1, min(3, len(product_terms))):
            _add_check(
                checks,
                key="product_fit",
                label="Product Fit",
                status="pass",
                message="The linked product is clearly reflected in the draft.",
                hint=_PRODUCT_FIT_HINT,
            )
        elif product_title.strip():
            score -= _add_check(
                checks,
                key="product_fit",
                label="Product Fit",
                status="warn",
                message="A product is selected, but the draft barely references the product name or terms.",
                impact=9,
                hint=_PRODUCT_FIT_HINT,
            )
        else:
            score -= _add_check(
                checks,
                key="product_fit",
                label="Product Fit",
                status="warn",
                message="A product is selected, but no product title was available for relevance checks.",
                impact=5,
                hint=_PRODUCT_FIT_HINT,
            )

        if ending_has_cta:
            _add_check(
                checks,
                key="product_cta",
                label="Product CTA",
                status="pass",
                message="The product link or publish-time CTA gives the reader a next step.",
                hint=_PRODUCT_CTA_HINT,
            )
        else:
            score -= _add_check(
                checks,
                key="product_cta",
                label="Product CTA",
                status="warn",
                message="Add a product-specific CTA near the end.",
                impact=6,
                hint=_PRODUCT_CTA_HINT,
            )

    _READ_HINT = "Target: ≤20% long sentences (>34 words) · no single paragraph >120 words"
    sentences = _sentence_parts(content)
    long_sentences = [s for s in sentences if len(_words(s)) > 34]
    long_paragraphs = [p for p in _paragraphs(content) if len(_words(p)) > 120]
    long_sentence_ratio = len(long_sentences) / max(1, len(sentences))
    if long_sentence_ratio <= 0.20 and not long_paragraphs:
        _add_check(
            checks,
            key="readability",
            label="Readability",
            status="pass",
            message="Sentence and paragraph lengths look readable.",
            hint=_READ_HINT,
        )
    elif long_sentence_ratio <= 0.35 and len(long_paragraphs) <= 1:
        score -= _add_check(
            checks,
            key="readability",
            label="Readability",
            status="warn",
            message="Some long sentences or dense paragraphs may slow readers down.",
            impact=7,
            hint=_READ_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="readability",
            label="Readability",
            status="fail",
            message="Too many long sentences or oversized paragraphs. Break up the copy.",
            impact=14,
            hint=_READ_HINT,
        )

    _ARTIFACT_HINT = "Target: no placeholders, markdown fences, leaked prompts, or title repeated at start"
    artifact_hits = sorted({match.group(0).strip() for match in _ARTIFACT_RE.finditer(content)})
    normalized_title = _normalize_text(title)
    normalized_start = _normalize_text(content[: max(120, len(title) + 40)])
    if normalized_title and normalized_start.startswith(normalized_title):
        artifact_hits.append("title repeated at start")
    if artifact_hits:
        score -= _add_check(
            checks,
            key="ai_artifacts",
            label="AI Artifacts",
            status="fail",
            message=f"Remove generated-content artifacts: {', '.join(artifact_hits[:4])}.",
            impact=16,
            hint=_ARTIFACT_HINT,
        )
    else:
        _add_check(
            checks,
            key="ai_artifacts",
            label="AI Artifacts",
            status="pass",
            message="No obvious placeholders, prompt leakage, fences, or repeated title detected.",
            hint=_ARTIFACT_HINT,
        )

    _SEO_HINT = "Target: at least one bullet list (-) and a FAQ/question section (links/CTAs are auto-added at publish)"
    seo_gaps: list[str] = []
    if not _BULLET_RE.search(content):
        seo_gaps.append("bullet lists")
    if not _FAQ_RE.search(content):
        seo_gaps.append("FAQ/question section")
    if not seo_gaps:
        _add_check(
            checks,
            key="seo_completeness",
            label="SEO Completeness",
            status="pass",
            message="The draft includes scannable elements and a useful reader path.",
            hint=_SEO_HINT,
        )
    elif len(seo_gaps) <= 1:
        score -= _add_check(
            checks,
            key="seo_completeness",
            label="SEO Completeness",
            status="warn",
            message=f"Consider adding: {', '.join(seo_gaps)}.",
            impact=5,
            hint=_SEO_HINT,
        )
    else:
        score -= _add_check(
            checks,
            key="seo_completeness",
            label="SEO Completeness",
            status="warn",
            message=f"The draft is missing useful SEO elements: {', '.join(seo_gaps)}.",
            impact=10,
            hint=_SEO_HINT,
        )

    _TRUST_HINT = "Target: no medical/legal guarantee claims (cure, guaranteed, FDA approved, etc.) · no sweeping absolutes"
    trust_hits = sorted({match.group(0).strip() for match in _TRUST_CLAIM_RE.finditer(combined_text)})
    absolute_hits = sorted({match.group(0).strip() for match in _ABSOLUTE_CLAIM_RE.finditer(combined_text)})
    if trust_hits:
        score -= _add_check(
            checks,
            key="trust_safety",
            label="Trust & Claim Safety",
            status="fail",
            message=f"Risky or regulated claims detected: {', '.join(trust_hits[:4])}. Soften or substantiate them.",
            impact=18,
            hint=_TRUST_HINT,
        )
    elif absolute_hits:
        score -= _add_check(
            checks,
            key="trust_safety",
            label="Trust & Claim Safety",
            status="warn",
            message=f"Absolute claims detected: {', '.join(absolute_hits[:4])}. Use more careful wording.",
            impact=8,
            hint=_TRUST_HINT,
        )
    else:
        _add_check(
            checks,
            key="trust_safety",
            label="Trust & Claim Safety",
            status="pass",
            message="No obvious risky claims or guarantee language detected.",
            hint=_TRUST_HINT,
        )

    score = max(0, min(100, score))

    publish_blocked = score < 55 or body_words < 250 or not title or not summary or not content
    if publish_blocked:
        verdict = "blocked"
        verdict_label = "Blocked"
    elif score >= 80:
        verdict = "ready"
        verdict_label = "Ready"
    else:
        verdict = "review"
        verdict_label = "Needs Review"

    return QualityReport(
        score=score,
        verdict=verdict,
        verdict_label=verdict_label,
        publish_blocked=publish_blocked,
        word_count=body_words,
        heading_count=heading_count,
        paragraph_count=paragraph_count,
        image_count=image_count,
        duplicate_similarity=0.0,
        duplicate_title="",
        duplicate_article_url="",
        checks=checks,
    )


async def review_draft(
    *,
    store_id: str,
    title: str,
    summary: str,
    content: str,
    keywords: Optional[list[str]] = None,
    prompt_text: str = "",
    product_url: str = "",
    product_title: str = "",
    image_count: int = 0,
    history_limit: int = 40,
    exclude_article_url: str = "",
    exclude_article_id: str = "",
) -> QualityReport:
    report = evaluate_draft(
        title=title,
        summary=summary,
        content=content,
        keywords=keywords,
        prompt_text=prompt_text,
        product_url=product_url,
        product_title=product_title,
        image_count=image_count,
    )

    current_text = _normalize_text(f"{title}\n{summary}\n{content}")[:6000]
    current_title = _normalize_text(title)
    current_summary = _normalize_text(summary)
    rows = await db.get_recent_generations(store_id=store_id, limit=history_limit)

    best_row: Optional[dict] = None
    best_similarity = 0.0
    best_title_row: Optional[dict] = None
    best_title_similarity = 0.0
    best_summary_row: Optional[dict] = None
    best_summary_similarity = 0.0
    for row in rows:
        if exclude_article_url and row.get("article_url") == exclude_article_url:
            continue
        if exclude_article_id and str(row.get("article_id") or "") == exclude_article_id:
            continue
        existing_text = _normalize_text(
            "\n".join(
                [
                    row.get("title", ""),
                    row.get("summary", ""),
                    row.get("content_text", ""),
                ]
            )
        )[:6000]
        title_similarity = _similarity(current_title, _normalize_text(row.get("title", "")))
        summary_similarity = _similarity(current_summary, _normalize_text(row.get("summary", "")))
        text_similarity = _similarity(current_text, existing_text)
        if title_similarity > best_title_similarity:
            best_title_similarity = title_similarity
            best_title_row = row
        if summary_similarity > best_summary_similarity:
            best_summary_similarity = summary_similarity
            best_summary_row = row
        combined_similarity = max(
            text_similarity,
            (text_similarity * 0.60) + (title_similarity * 0.25) + (summary_similarity * 0.15),
        )
        if combined_similarity > best_similarity:
            best_similarity = combined_similarity
            best_row = row

    _TITLE_ORIG_HINT = "Target: <82% similarity to any recent title"
    if best_title_row and best_title_similarity >= 0.94:
        report.score -= _add_check(
            report.checks,
            key="title_originality",
            label="Title Originality",
            status="fail",
            message=(
                f"This title is almost the same as a previous post, "
                f"\"{best_title_row.get('title', 'Untitled')}\"."
            ),
            impact=16,
            hint=_TITLE_ORIG_HINT,
        )
    elif best_title_row and best_title_similarity >= 0.82:
        report.score -= _add_check(
            report.checks,
            key="title_originality",
            label="Title Originality",
            status="warn",
            message=(
                f"This title is close to a previous post, "
                f"\"{best_title_row.get('title', 'Untitled')}\" ({best_title_similarity:.0%} similar)."
            ),
            impact=8,
            hint=_TITLE_ORIG_HINT,
        )
    else:
        _add_check(
            report.checks,
            key="title_originality",
            label="Title Originality",
            status="pass",
            message="Title looks distinct from recent generated posts.",
            hint=_TITLE_ORIG_HINT,
        )

    _EXCERPT_ORIG_HINT = "Target: <90% similarity to any recent excerpt"
    if best_summary_row and best_summary_similarity >= 0.90:
        report.score -= _add_check(
            report.checks,
            key="excerpt_originality",
            label="Excerpt Originality",
            status="warn",
            message="The excerpt is very similar to a recent generated excerpt. Make it more specific.",
            impact=8,
            hint=_EXCERPT_ORIG_HINT,
        )
    else:
        _add_check(
            report.checks,
            key="excerpt_originality",
            label="Excerpt Originality",
            status="pass",
            message="Excerpt is not a close match to recent generated excerpts.",
            hint=_EXCERPT_ORIG_HINT,
        )

    _DUPL_HINT = "Target: <72% combined content similarity to recent posts · 72–87% (warn) · ≥88% (fail)"
    if best_row and best_similarity >= 0.88:
        report.score -= _add_check(
            report.checks,
            key="internal_duplication",
            label="Internal Similarity",
            status="fail",
            message=(
                f"This draft is very similar to a previous post, \"{best_row.get('title', 'Untitled')}\" "
                f"({best_similarity:.0%} similarity). Rewrite before publishing."
            ),
            impact=26,
            hint=_DUPL_HINT,
        )
        report.duplicate_similarity = best_similarity
        report.duplicate_title = best_row.get("title", "")
        report.duplicate_article_url = best_row.get("article_url", "") or ""
    elif best_row and best_similarity >= 0.72:
        report.score -= _add_check(
            report.checks,
            key="internal_duplication",
            label="Internal Similarity",
            status="warn",
            message=(
                f"This draft overlaps with a previous post, \"{best_row.get('title', 'Untitled')}\" "
                f"({best_similarity:.0%} similarity). Tighten the angle before publishing."
            ),
            impact=12,
            hint=_DUPL_HINT,
        )
        report.duplicate_similarity = best_similarity
        report.duplicate_title = best_row.get("title", "")
        report.duplicate_article_url = best_row.get("article_url", "") or ""
    else:
        _add_check(
            report.checks,
            key="internal_duplication",
            label="Internal Similarity",
            status="pass",
            message="No strong overlap with recent posts from this store.",
            hint=_DUPL_HINT,
        )

    _recompute_verdict(report, title=title, summary=summary, content=content)

    # Emit a single JSON line to quality.log for later analysis
    try:
        _quality_log.info(json.dumps({
            "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "store_id": store_id,
            "title": title,
            "score": report.score,
            "verdict": report.verdict,
            "publish_blocked": report.publish_blocked,
            "word_count": report.word_count,
            "heading_count": report.heading_count,
            "paragraph_count": report.paragraph_count,
            "image_count": report.image_count,
            "duplicate_similarity": round(report.duplicate_similarity, 3),
            "duplicate_title": report.duplicate_title,
            "checks": [
                {"key": c.key, "status": c.status, "impact": c.impact, "message": c.message}
                for c in report.checks
            ],
        }, ensure_ascii=False))
    except Exception:
        pass

    return report