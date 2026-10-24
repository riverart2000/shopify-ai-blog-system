"""Deterministic text helpers shared by captions, guards and decisions.

Nothing here calls a model. These functions are what make the AI output safe
to publish even when the model ignores its instructions: JSON extraction,
emoji and hashtag handling, per-network length measurement and trimming that
never cuts through a URL or hashtag.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Reasoning / JSON extraction
# ---------------------------------------------------------------------------

_REASONING_BLOCKS = (
    re.compile(r"<(think|thinking|reasoning|thought)>.*?</\1>", re.S | re.I),
    re.compile(r"<\|channel\|>\s*(?:analysis|thought|commentary)\s*<\|message\|>.*?(?:<\|end\|>|(?=<\|start\|>)|(?=<\|channel\|>))", re.S),
    re.compile(r"<\|channel>\s*thought.*?<channel\|>", re.S),
)
_ORPHAN_CLOSE = re.compile(r"</(?:think|thinking|reasoning|thought)>", re.I)
_UNCLOSED_OPEN = re.compile(r"<(?:think|thinking|reasoning|thought)>.*\Z", re.S | re.I)
_HARMONY_FINAL = re.compile(r"<\|channel\|>\s*final\s*<\|message\|>", re.S)
_SPECIAL_TOKENS = re.compile(r"<\|[a-z_]{1,24}\|>|<\|[a-z_]{1,24}>|<[a-z_]{1,24}\|>", re.I)
_FENCE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> style reasoning and chat-template tokens."""
    value = str(text or "")
    final = list(_HARMONY_FINAL.finditer(value))
    if final:
        value = value[final[-1].end():]
    for pattern in _REASONING_BLOCKS:
        value = pattern.sub("", value)
    closes = list(_ORPHAN_CLOSE.finditer(value))
    if closes:  # some servers drop the opening tag: keep what follows the last close
        value = value[closes[-1].end():]
    value = _UNCLOSED_OPEN.sub("", value)
    value = _SPECIAL_TOKENS.sub("", value)
    return value.strip()


def _balanced_blocks(text: str, max_starts: int = 25) -> Iterable[str]:
    starts = 0
    index = 0
    while starts < max_starts:
        positions = [pos for pos in (text.find("{", index), text.find("[", index)) if pos >= 0]
        if not positions:
            return
        start = min(positions)
        starts += 1
        depth = 0
        in_string = False
        escaped = False
        for pos in range(start, len(text)):
            char = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    yield text[start:pos + 1]
                    break
        index = start + 1


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply.

    Handles reasoning tags, ```json fences, prose around the JSON and trailing
    commas. Raises ValueError when nothing parses.
    """
    cleaned = strip_reasoning(text)
    candidates = [match.group(1).strip() for match in _FENCE.finditer(cleaned)]
    candidates.append(cleaned)
    for candidate in candidates:
        for variant in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            try:
                return json.loads(variant)
            except ValueError:
                pass
        for block in _balanced_blocks(candidate):
            for variant in (block, _TRAILING_COMMA.sub(r"\1", block)):
                try:
                    return json.loads(variant)
                except ValueError:
                    continue
    excerpt = " ".join(cleaned.split())[:160]
    raise ValueError(f"The reply did not contain valid JSON (starts with: {excerpt!r})")


# ---------------------------------------------------------------------------
# Emojis
# ---------------------------------------------------------------------------

_EMOJI_BASE = (
    "\U0001F000-\U0001FAFF"   # pictographs, emoticons, transport, flags, skin tones…
    "☀-➿"           # misc symbols + dingbats (☀ ✨ ❤ ✔)
    "⌀-⏿"           # ⌚ ⏰ ⏳
    "⬀-⯿"           # ⭐ ⬆
    "〰〽㊗㊙"
)
_EMOJI_MOD = "️\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F"
_EMOJI_TEXT_DEFAULT = "©®‼⁉™ℹ↔-↙↩↪Ⓜ▪▫▶◀◻-◾⤴⤵"
EMOJI_RE = re.compile(
    rf"(?:[\U0001F1E6-\U0001F1FF]{{2}})"
    rf"|(?:[0-9#*]️?⃣)"
    rf"|(?:[{_EMOJI_BASE}][{_EMOJI_MOD}]*(?:‍[{_EMOJI_BASE}♀♂][{_EMOJI_MOD}]*)*)"
    rf"|(?:[{_EMOJI_TEXT_DEFAULT}]️)"
)
_EMOJI_DEBRIS = re.compile("[‍️⃣]")


def count_emojis(text: str) -> int:
    return len(EMOJI_RE.findall(text or ""))


def limit_emojis(text: str, max_count: int) -> str:
    """Keep the first `max_count` emojis and remove the rest."""
    kept = 0

    def _replace(match: re.Match) -> str:
        nonlocal kept
        if kept < max_count:
            kept += 1
            return match.group(0)
        return ""

    value = EMOJI_RE.sub(_replace, text or "")
    if max_count <= 0:
        value = _EMOJI_DEBRIS.sub("", value)
    return tidy(value)


def strip_emojis(text: str) -> str:
    return limit_emojis(text, 0)


# ---------------------------------------------------------------------------
# URLs, hashtags, HTML, whitespace
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
_TLDS = r"(?:com|co\.uk|org\.uk|uk|org|net|io|shop|store|co|info|health|life|eu|me|app|ly)"
DOMAIN_RE = re.compile(rf"(?<![@\w/.-])[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.{_TLDS}\b(?:/[^\s]*)?", re.I)
HASHTAG_RE = re.compile(r"(?<![\w&/#])#(\w[\w-]*)", re.UNICODE)
_TRAILING_TAG_LINE = re.compile(r"^(?:\s*#\w[\w-]*[,]?)+\s*$", re.UNICODE)
_LINK_REFERENCE = re.compile(
    r"\blink\s+(?:is\s+)?in\s+(?:our\s+|my\s+|the\s+)?(?:bio|profile|first\s+comment|comments)\b"
    r"|\b(?:tap|click|hit|use|follow|via)\s+(?:the\s+|our\s+|this\s+)?links?\b"
    r"|\blinks?\s+(?:below|above)\b",
    re.I,
)
_DANGLING_CTA_END = re.compile(r"[ \t]*(?:[:→➡👉👇⬇]|[-–—][ \t]*)+[ \t]*$", re.M)
_HTML_BREAK = re.compile(r"<\s*(?:br|/p|/li|/h[1-6]|/div)\s*/?>", re.I)
_HTML_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?…])")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
_DANGLING_PUNCT_LINE = re.compile(r"^[ \t]*[-–—:|•,;]+[ \t]*$", re.M)


def tidy(text: str) -> str:
    """Collapse spaces, fix space-before-punctuation and blank-line runs."""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _SPACES.sub(" ", value)
    value = _EMPTY_BRACKETS.sub("", value)
    value = _SPACE_BEFORE_PUNCT.sub(r"\1", value)
    value = _DANGLING_PUNCT_LINE.sub("", value)
    value = "\n".join(line.strip() for line in value.split("\n"))
    value = _MANY_NEWLINES.sub("\n\n", value)
    return value.strip()


def strip_html(text: str) -> str:
    value = _HTML_BREAK.sub("\n", str(text or ""))
    value = _HTML_TAG.sub(" ", value)
    return tidy(html.unescape(value))


def strip_urls(text: str) -> str:
    return tidy(URL_RE.sub("", text or ""))


def remove_urls(text: str) -> str:
    """Remove URLs; a line that was only a link plus a few CTA words ("Shop here:") goes entirely."""
    kept: list[str] = []
    for line in (text or "").split("\n"):
        if URL_RE.search(line):
            rest = URL_RE.sub("", line)
            if len(re.findall(r"\w+", rest)) <= 4:
                continue
            line = rest
        kept.append(line)
    return tidy(_DANGLING_CTA_END.sub("", "\n".join(kept)))


def remove_link_in_bio(text: str) -> str:
    """Drop every sentence that points at a link ("link in bio", "tap the link"…)."""
    lines: list[str] = []
    for line in (text or "").split("\n"):
        sentences = re.split(r"(?<=[.!?…])\s+", line)
        lines.append(" ".join(sentence for sentence in sentences if not _LINK_REFERENCE.search(sentence)))
    return tidy(_DANGLING_CTA_END.sub("", "\n".join(lines)))


def extract_hashtags(text: str) -> tuple[str, list[str]]:
    """Remove hashtags from `text` and return (clean_text, tags).

    Lines made only of hashtags are dropped; a hashtag inside a sentence keeps
    its word ("our #Magnesium blend" → "our Magnesium blend").
    """
    tags: list[str] = []
    kept_lines: list[str] = []
    for line in (text or "").split("\n"):
        if line.strip() and _TRAILING_TAG_LINE.match(line):
            tags.extend(HASHTAG_RE.findall(line))
            continue
        # trailing run of hashtags at the end of a sentence line
        trailing = re.search(r"(?:\s+#\w[\w-]*)+\s*$", line, re.UNICODE)
        if trailing and trailing.start() > 0:
            tags.extend(HASHTAG_RE.findall(trailing.group(0)))
            line = line[:trailing.start()]
        tags.extend(HASHTAG_RE.findall(line))
        kept_lines.append(HASHTAG_RE.sub(lambda m: m.group(1), line))
    return tidy("\n".join(kept_lines)), tags


def normalise_hashtag(tag: str) -> str:
    """'magnesium glycinate' / '#sleep-support' / 'women's health' → '#MagnesiumGlycinate' …

    Returns "" for tags without a letter or longer than 40 characters.
    """
    raw = str(tag or "").strip().lstrip("#＃").replace("'", "").replace("’", "")
    parts = [part for part in re.split(r"[\W_]+", raw, flags=re.UNICODE) if part]
    if not parts:
        return ""
    word = "".join(part[0].upper() + part[1:] for part in parts)
    if not any(char.isalpha() for char in word) or len(word) > 40:
        return ""
    return f"#{word}"


def normalise_hashtags(tags: Iterable[str], cap: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalised = normalise_hashtag(tag)
        key = normalised.casefold()
        if not normalised or key in seen:
            continue
        seen.add(key)
        result.append(normalised)
        if cap is not None and len(result) >= cap:
            break
    return result


def remove_terms(text: str, terms: Iterable[str]) -> tuple[str, list[str]]:
    """Remove whole-word, case-insensitive occurrences of each term."""
    value = text or ""
    removed: list[str] = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        pattern = re.compile(rf"(?<!\w)#?{re.escape(term)}(?!\w)", re.I)
        if pattern.search(value):
            removed.append(term)
            value = pattern.sub("", value)
    return (tidy(value) if removed else value), removed


def find_terms(text: str, terms: Iterable[str]) -> list[str]:
    found = []
    for term in terms:
        term = str(term or "").strip()
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text or "", re.I):
            found.append(term)
    return found


# ---------------------------------------------------------------------------
# Length measurement and trimming
# ---------------------------------------------------------------------------

X_URL_LENGTH = 23
_X_SINGLE_WEIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def _x_char_weight(char: str) -> int:
    code = ord(char)
    for low, high in _X_SINGLE_WEIGHT:
        if low <= code <= high:
            return 1
    return 2


def x_length(text: str) -> int:
    """X/Twitter weighted length: every URL counts 23, emoji/CJK count 2."""
    value = text or ""
    spans = sorted(
        [(m.start(), m.end()) for m in URL_RE.finditer(value)]
        + [(m.start(), m.end()) for m in DOMAIN_RE.finditer(value)]
    )
    total = 0
    position = 0
    for start, end in spans:
        if start < position:
            continue
        total += sum(_x_char_weight(char) for char in value[position:start]) + X_URL_LENGTH
        position = end
    total += sum(_x_char_weight(char) for char in value[position:])
    return total


def measure_for(provider: str) -> Callable[[str], int]:
    return x_length if (provider or "").strip().lower() == "x" else len


_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)|\n")
_TRAIL_PUNCT = " \t\n,;:–—-([{\"'“‘"


def _largest_fitting(positions: list[int], build: Callable[[int], str], limit: int, measure: Callable[[str], int]) -> str:
    low, high, best = 0, len(positions) - 1, ""
    while low <= high:
        middle = (low + high) // 2
        candidate = build(positions[middle])
        if measure(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def trim_to_limit(text: str, limit: int, measure: Callable[[str], int] = len, *, ellipsis: str = "…") -> str:
    """Shorten `text` to fit `limit`, preferring a sentence end, then a word end.

    URLs are never split (cuts only happen at whitespace unless a single word
    is longer than the limit).
    """
    value = (text or "").strip()
    if limit <= 0:
        return ""
    if measure(value) <= limit:
        return value
    sentence_ends = [match.end() for match in _SENTENCE_END.finditer(value)]
    by_sentence = _largest_fitting(sentence_ends, lambda end: value[:end].rstrip(), limit, measure)
    spaces = [match.start() for match in re.finditer(r"\s", value)]
    by_word = _largest_fitting(
        spaces, lambda pos: (value[:pos].rstrip(_TRAIL_PUNCT) + ellipsis) if value[:pos].strip() else "", limit, measure,
    )
    if by_sentence and len(by_sentence) >= 0.55 * len(by_word):
        return by_sentence
    if by_word.strip(ellipsis).strip():
        return by_word
    # A single very long word or a script without spaces: cut characters,
    # stepping back to the start of any URL the cut would fall into.
    url_spans = [(m.start(), m.end()) for m in URL_RE.finditer(value)]
    low, high, best = 0, len(value), ""
    while low <= high:
        middle = (low + high) // 2
        cut = middle
        for start, end in url_spans:
            if start < cut < end:
                cut = start
        candidate = value[:cut].rstrip(_TRAIL_PUNCT)
        candidate = candidate + ellipsis if candidate else ""
        if measure(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def truncate_words(text: str, limit: int) -> str:
    """Trim short on-image text at a word boundary without an ellipsis."""
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    cut = value[:limit + 1]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut[:limit].rstrip(_TRAIL_PUNCT)
