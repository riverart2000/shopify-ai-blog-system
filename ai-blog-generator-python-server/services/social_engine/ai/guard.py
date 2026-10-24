"""Deterministic content guard run on every caption before approval/publishing.

Severity "block" means the post must not go out as is; `hold=True` means a
person has to look at it even in auto-approval mode.
"""
from __future__ import annotations

import re

from services.content_claims import find_claims

from ..models import profile_for
from ..settings import EngineSettings
from .base import CaptionResult, GuardIssue, GuardResult
from .text import HASHTAG_RE, URL_RE, find_terms, measure_for

CLAIM_CODES = frozenset({"health_claim", "health_claim_review", "risky_wording"})
INSTAGRAM_HASHTAG_LIMIT = 30

_RISKY_WORDING: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"\b(?:scientifically|medically)\s+proven\b", re.I), "proof claim", "block"),
    (re.compile(r"\bboost(?:s|ing|ed)?\s+(?:your\s+|the\s+)?immun\w*", re.I), "immunity-boosting claim", "warning"),
    (re.compile(r"\bdetox\w*\b", re.I), "detox claim", "warning"),
    (re.compile(r"\bmiracle\b", re.I), "'miracle' wording", "warning"),
    (re.compile(r"\bguaranteed\s+(?:results?|to)\b|\bresults?\s+guaranteed\b", re.I), "guaranteed results", "warning"),
    (re.compile(r"\b(?:doctors?|gps?|nutritionists?|dietitians?|pharmacists?)[- ]?(?:recommended|approved|endorsed)\b", re.I),
     "professional endorsement", "warning"),
    (re.compile(r"\bheal(?:s|ing|ed)?\b", re.I), "healing claim", "warning"),
    (re.compile(r"\banti[- ]?(?:ageing|aging|anxiety|inflammatory|depressant|stress)\b", re.I), "'anti-' claim", "warning"),
    (re.compile(r"\b(?:burns?|melts?|blasts?)\s+(?:belly\s+|body\s+)?fat\b|\bweight[- ]loss\b|\blose\s+weight\b", re.I),
     "weight-loss claim", "warning"),
    (re.compile(r"\bsuperfoods?\b", re.I), "'superfood' wording", "warning"),
    # Authorised claims are "contributes to (the) normal …"; anything that ties the product
    # to a body system in other words is an unauthorised paraphrase.
    (re.compile(
        r"\b(?:support|boost|improv|enhanc|promot|optimi[sz]|nourish|fuel|strengthen|balanc|for)\w*\s+"
        r"(?:your\s+|the\s+|a\s+|healthy\s+|better\s+)*"
        r"(?:nervous\s+system|immune\s+system|immunity|muscles?|energy\s+levels|sleep|mood|brain|heart|gut|"
        r"digestion|metabolism|hormones?|joints?|bones?|focus|memory|stress\s+response|recovery)\b"
        r"(?!\s+(?:routines?|rituals?|time|schedules?|space|habits?|days?|playlists?))", re.I),
     "unauthorised claim paraphrase", "warning"),
    (re.compile(
        r"\b(?:scientific\s+|clinical\s+)?(?:evidence|science|research|studies|experts?|doctors?)\s+"
        r"(?:shows?|suggests?|proves?|confirms?|says?|agrees?)\b", re.I),
     "evidence attribution", "warning"),
)
# content_claims.find_claims needs a generic subject ("this", "it", "supplement"…), so
# "Our magnesium cures insomnia" or "Fights fatigue" slip through. Social captions are
# always about the product, so medicinal wording is checked here without a subject.
_MEDICINAL_VERB = (
    r"(?:cur(?:e|es|ed|ing)|treat(?:s|ed|ing|ment)?|prevent(?:s|ed|ing|ion)?|heal(?:s|ed|ing)?|"
    r"reliev(?:e|es|ed|ing)|relief|fight(?:s|ing)?|beat(?:s|ing)?|combat(?:s|ing)?|eliminat(?:e|es|ed|ing)|"
    r"revers(?:e|es|ed|ing)|fix(?:es|ed|ing)?|get\s+rid\s+of|banish(?:es|ed|ing)?|ease(?:s|d)?|soothe(?:s|d)?|"
    r"reduc(?:e|es|ed|ing)|tackl(?:e|es|ed|ing)|help(?:s|ed|ing)?\s+(?:with|against))"
)
_CONDITION = (
    r"(?:insomnia|sleep\s+disorders?|sleeplessness|anxiety|depression|panic\s+attacks?|stress|burn-?out|"
    r"arthritis|joint\s+pain|cancer|tumou?rs?|diabetes|blood\s+sugar|(?:high\s+)?blood\s+pressure|hypertension|"
    r"cholesterol|heart\s+disease|diseases?|disorders?|illness(?:es)?|migraines?|headaches?|infections?|colds?|"
    r"flu|covid|viruses?|inflammation|eczema|psoriasis|acne|ibs|bloating|constipation|adhd|dementia|"
    r"alzheimer['’]?s?|osteoporosis|cramps?|pain|aches?|symptoms?|deficienc(?:y|ies)|fatigue|exhaustion|"
    r"hair\s+loss|menopause|pms|tiredness)"
)
_MEDICINAL_FORWARD = re.compile(rf"\b{_MEDICINAL_VERB}\b[^.!?\n]{{0,60}}?\b{_CONDITION}\b", re.I)
_MEDICINAL_BACKWARD = re.compile(
    rf"\b{_CONDITION}\b[^.!?\n]{{0,25}}?\b(?:relief|remedy|cure|treatment|solution|killer|buster)\b", re.I,
)
_NEGATED = re.compile(
    r"\b(?:not|never|no|isn['’]?t|doesn['’]?t|does\s+not|cannot|can['’]?t|without)\b[^.!?\n]{0,30}$", re.I,
)
_SENTENCES = re.compile(r"[^.!?\n]+[.!?]*")
_URGENCY = re.compile(r"\b(?:only|just)\s+\d+\s+left\b|\bselling\s+fast\b|\balmost\s+gone\b", re.I)
_PLACEHOLDER = re.compile(
    r"\b(?:PRODUCT_(?:TITLE|DESC|PRICE|TAGS|URL)|STORE_(?:URL|NAME)|TAG_CLOUD|PROMO_TEXT|COUPON_CODE)\b|\{\{[^}]*\}\}"
)
_BIO = re.compile(r"\bbio\b", re.I)


def _medicinal_claims(text: str, already: set[str]) -> list[tuple[str, str]]:
    """(trigger, sentence) for medicinal wording find_claims did not report."""
    found: list[tuple[str, str]] = []
    for match in _SENTENCES.finditer(text or ""):
        sentence = " ".join(match.group(0).split())
        if not sentence or sentence.casefold() in already:
            continue
        for pattern in (_MEDICINAL_FORWARD, _MEDICINAL_BACKWARD):
            hit = pattern.search(sentence)
            if hit and not _NEGATED.search(sentence[:hit.start()]):
                found.append((hit.group(0), sentence))
                break
    return found


def claim_issues(text: str, channel_id: str, where: str) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    reported: set[str] = set()
    for finding in find_claims(text):
        blocking = finding.risk == "high" and not finding.cited
        reported.add(finding.excerpt.casefold())
        issues.append(GuardIssue(
            code="health_claim" if blocking else "health_claim_review",
            message=f'{finding.kind.replace("_", " ").capitalize()} ("{finding.trigger}") in {where}: "{finding.excerpt}"',
            severity="block" if blocking else "warning",
            channel_id=channel_id,
        ))
    for trigger, sentence in _medicinal_claims(text, reported):
        issues.append(GuardIssue(
            code="health_claim", message=f'Medicinal claim ("{trigger}") in {where}: "{sentence[:300]}"',
            severity="block", channel_id=channel_id,
        ))
    for pattern, label, severity in _RISKY_WORDING:
        match = pattern.search(text)
        if match:
            issues.append(GuardIssue(
                code="risky_wording", message=f'{label.capitalize()} ("{match.group(0)}") in {where}',
                severity=severity, channel_id=channel_id,
            ))
    match = _URGENCY.search(text)
    if match:
        issues.append(GuardIssue(
            code="urgency_wording", message=f'Urgency wording ("{match.group(0)}") in {where} — make sure it is true',
            severity="warning", channel_id=channel_id,
        ))
    return issues


def check_captions(result: CaptionResult, *, settings: EngineSettings) -> GuardResult:
    issues: list[GuardIssue] = []
    banned = settings.captions.banned_terms
    link_policy = settings.captions.link_policy

    on_image = {
        "headline": result.headline, "subheadline": result.subheadline, "cta": result.cta,
        "hook": result.hook, "alt text": result.alt_text,
    }
    for label, text in on_image.items():
        if not text:
            continue
        issues.extend(claim_issues(text, "", f"the {label}"))
        for term in find_terms(text, banned):
            issues.append(GuardIssue("banned_term", f"Banned term '{term}' in the {label}", "block"))
        if _PLACEHOLDER.search(text):
            issues.append(GuardIssue("placeholder_left", f"Unfilled placeholder in the {label}: {text[:80]}", "block"))

    post_has_link = any(
        URL_RE.search(item.caption) or URL_RE.search(item.first_comment)
        for item in result.channel_captions.values()
    )
    for channel_id, item in result.channel_captions.items():
        profile = profile_for(item.provider)
        where = f"the {profile.label if profile.key != 'generic' else item.provider} caption"
        caption = item.caption or ""
        if not caption.strip():
            issues.append(GuardIssue("empty_caption", f"{where.capitalize()} is empty", "block", channel_id))
            continue
        combined = "\n".join(part for part in (caption, item.title, item.first_comment) if part)
        issues.extend(claim_issues(combined, channel_id, where))
        for term in find_terms(combined, banned):
            issues.append(GuardIssue("banned_term", f"Banned term '{term}' in {where}", "block", channel_id))
        if _PLACEHOLDER.search(combined):
            issues.append(GuardIssue("placeholder_left", f"Unfilled placeholder in {where}", "block", channel_id))

        length = measure_for(item.provider)(caption)
        if length > profile.max_length:
            issues.append(GuardIssue(
                "too_long", f"{where.capitalize()} is {length} characters; {profile.label} allows {profile.max_length}",
                "block", channel_id,
            ))
        if profile.title_max and len(item.title) > profile.title_max:
            issues.append(GuardIssue(
                "title_too_long", f"{profile.label} title is {len(item.title)} characters (max {profile.title_max})",
                "block", channel_id,
            ))

        hashtag_count = len(HASHTAG_RE.findall(caption))
        if profile.key.startswith("instagram") and hashtag_count > INSTAGRAM_HASHTAG_LIMIT:
            issues.append(GuardIssue(
                "too_many_hashtags", f"{where.capitalize()} has {hashtag_count} hashtags; Instagram rejects more than 30",
                "block", channel_id,
            ))
        elif hashtag_count > profile.hashtags_max:
            issues.append(GuardIssue(
                "too_many_hashtags", f"{where.capitalize()} has {hashtag_count} hashtags (recommended max {profile.hashtags_max})",
                "warning", channel_id,
            ))

        link_expected = post_has_link and link_policy != "none" and not (
            link_policy == "all_except_instagram" and profile.key.startswith("instagram")
        )
        if profile.link_policy == "settings_link" and URL_RE.search(caption):
            issues.append(GuardIssue(
                "url_in_caption", f"{where.capitalize()} contains a URL; {profile.label} takes the link in its settings",
                "warning", channel_id,
            ))
        elif link_expected and profile.link_policy == "inline" and not URL_RE.search(caption):
            issues.append(GuardIssue("missing_link", f"{where.capitalize()} has no product link", "warning", channel_id))
        elif link_expected and profile.link_policy == "bio" and not (_BIO.search(caption) or URL_RE.search(item.first_comment)):
            issues.append(GuardIssue(
                "missing_link", f"{where.capitalize()} does not point to the link in bio", "warning", channel_id,
            ))

    claims_found = any(issue.code in CLAIM_CODES for issue in issues)
    hold = any(issue.severity == "block" for issue in issues) or (claims_found and settings.guards.hold_health_claims)
    return GuardResult(hold=hold, issues=issues)
