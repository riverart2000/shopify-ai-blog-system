"""Deterministic health/research claim checks shared by generation and SEO audits.

The checks inspect complete sentences and nearby medical concepts. This avoids
treating ordinary uses such as "treat this as a reminder" or "prevents an
afternoon dip" as medical claims.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from bs4 import BeautifulSoup


_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_CITATION_RE = re.compile(
    r"https?://\S+|doi\.org/\S+|pubmed(?:\.ncbi\.nlm\.nih\.gov)?/\S+|\[[0-9]{1,3}\]",
    re.IGNORECASE,
)
_RESEARCH_RE = re.compile(
    r"\b(?:"
    r"clinically proven|"
    r"(?:a\s+)?(?:19|20)\d{2}\s+study\s+(?:shows?|showed|found|finds?|suggests?|demonstrates?|reports?)|"
    r"(?:a\s+|the\s+)?(?:study|research|clinical\s+(?:study|trial)|systematic\s+review|meta-analysis)\s+"
    r"(?:shows?|showed|found|finds?|suggests?|demonstrates?|proves?|reports?)"
    r")\b",
    re.IGNORECASE,
)
_HIGH_RISK_VERB_RE = re.compile(r"\b(?:cures?|diagnoses?)\b", re.IGNORECASE)
_THERAPEUTIC_VERB = r"(?:treat(?:s|ed|ing)?|prevent(?:s|ed|ing)?)"
_MEDICAL_CONCEPT = (
    r"(?:acne|anxiety|arthritis|asthma|blood\s+pressure|cancer|chronic\s+pain|"
    r"depression|diabetes|disease|disorder|eczema|fatigue|hair\s+loss|headaches?|"
    r"infection|inflammation|injur(?:y|ies)|insomnia|kidney\s+(?:damage|strain)|"
    r"migraines?|nausea|pain|symptoms?)"
)
_THERAPEUTIC_FORWARD_RE = re.compile(
    rf"\b(?P<verb>{_THERAPEUTIC_VERB})\b(?P<between>.{{0,55}}?)\b(?P<condition>{_MEDICAL_CONCEPT})\b",
    re.IGNORECASE,
)
_THERAPEUTIC_PASSIVE_RE = re.compile(
    rf"\b(?P<condition>{_MEDICAL_CONCEPT})\b(?P<between>.{{0,12}}?)\b"
    rf"(?P<verb>(?:is|are|can\s+be|may\s+be)\s+{_THERAPEUTIC_VERB})\b",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(?:may|might|could|can\s+help|may\s+help|might\s+help|could\s+help)\s+(?:to\s+)?$",
    re.IGNORECASE,
)
_CLAIM_SUBJECT_RE = re.compile(
    r"\b(?:this|these|it|they|product|device|tool|supplement|ingredient|cream|serum|"
    r"routine|exercise|movement|therapy|massage|technique|method|practice)\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|neither|nor|without|cannot|can['’]?t|won['’]?t|isn['’]?t|"
    r"aren['’]?t|wasn['’]?t|weren['’]?t|don['’]?t|doesn['’]?t|do not|does not)\b",
    re.IGNORECASE,
)
_ANALOGY_RE = re.compile(r"\b(?:treat(?:s|ed|ing)?|prevent(?:s|ed|ing)?)\b.{0,28}\b(?:as|like)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ClaimFinding:
    kind: str
    trigger: str
    excerpt: str
    cited: bool
    risk: str

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def text_with_link_targets(body_html: str) -> str:
    """Return visible article text while preserving external link destinations."""
    soup = BeautifulSoup(body_html or "", "html.parser")
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            label = link.get_text(" ", strip=True)
            link.replace_with(f"{label} ({href})")
    return soup.get_text(" ", strip=True)


def _clean_excerpt(sentence: str, limit: int = 300) -> str:
    value = " ".join((sentence or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _is_hedged(sentence: str, match_start: int) -> bool:
    prefix = sentence[max(0, match_start - 28):match_start]
    return bool(_HEDGE_RE.search(prefix))


def _is_positive_claim(sentence: str, match_start: int) -> bool:
    """Require a plausible claim subject and reject negations/analogies."""
    prefix = sentence[max(0, match_start - 40):match_start]
    clause_prefix = re.split(r"[,;:]", prefix)[-1]
    nearby = sentence[max(0, match_start - 35):match_start]
    if _NEGATION_RE.search(nearby):
        return False
    if _ANALOGY_RE.search(sentence):
        return False
    trigger_starts_sentence = not sentence[:match_start].strip()
    return trigger_starts_sentence or bool(_CLAIM_SUBJECT_RE.search(clause_prefix))


def find_claims(text: str) -> list[ClaimFinding]:
    """Find research assertions and contextual medical treatment claims."""
    findings: list[ClaimFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for sentence_match in _SENTENCE_RE.finditer(text or ""):
        sentence = " ".join(sentence_match.group(0).split())
        if not sentence:
            continue
        cited = bool(_CITATION_RE.search(sentence))

        for match in _RESEARCH_RE.finditer(sentence):
            trigger = match.group(0)
            kind = "clinical_claim" if trigger.lower() == "clinically proven" else "research_claim"
            key = (kind, trigger.lower(), sentence.lower())
            if key not in seen:
                seen.add(key)
                findings.append(ClaimFinding(
                    kind=kind,
                    trigger=trigger,
                    excerpt=_clean_excerpt(sentence),
                    cited=cited,
                    risk="high" if not cited or kind == "clinical_claim" else "review",
                ))

        for match in _HIGH_RISK_VERB_RE.finditer(sentence):
            if match.group(0).lower() == "cures" and re.search(r"\bmiracle\s+$", sentence[:match.start()], re.IGNORECASE):
                continue
            if not _is_positive_claim(sentence, match.start()):
                continue
            key = ("medical_claim", match.group(0).lower(), sentence.lower())
            if key not in seen:
                seen.add(key)
                findings.append(ClaimFinding(
                    kind="medical_claim",
                    trigger=match.group(0),
                    excerpt=_clean_excerpt(sentence),
                    cited=cited,
                    risk="high",
                ))

        for pattern in (_THERAPEUTIC_FORWARD_RE, _THERAPEUTIC_PASSIVE_RE):
            for match in pattern.finditer(sentence):
                if not _is_positive_claim(sentence, match.start("verb")):
                    continue
                trigger = match.group("verb")
                key = ("medical_claim", trigger.lower(), sentence.lower())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(ClaimFinding(
                    kind="medical_claim",
                    trigger=trigger,
                    excerpt=_clean_excerpt(sentence),
                    cited=cited,
                    risk="review" if _is_hedged(sentence, match.start("verb")) else "high",
                ))
    return findings
