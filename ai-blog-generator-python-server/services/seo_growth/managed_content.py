"""Deterministic detection and removal of SEO blocks created by this app.

The signatures below deliberately match only HTML emitted by historical
versions of ``shopify_client.publish_article``.  They must stay narrow: the
repair engine is allowed to remove our own generated artefacts, never arbitrary
merchant-authored content that merely happens to contain a hashtag.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


RULE_KEY = "remove_app_generated_keyword_blocks_v1"

_VISIBLE_STYLE = (
    "margin-top:40px;padding-top:24px;border-top:1px solid #e5e7eb;"
)
_HIDDEN_STYLE = (
    "font-size:1px;color:transparent;line-height:1;overflow:hidden;height:1px;"
)

# Historical visible blocks have one optional long-tail heading/list followed
# by up to two divs containing only spans.  Matching that known structure keeps
# the repair from consuming a later, unrelated div in the article.
_VISIBLE_BLOCK = re.compile(
    r"<div\s+style=(?P<q>['\"])" + re.escape(_VISIBLE_STYLE) + r"(?P=q)>"
    r"(?:\s*<p\b[^>]*>.*?</p>\s*<ul\b[^>]*>.*?</ul>)?"
    r"(?:\s*<div\b[^>]*>\s*(?:<span\b[^>]*>.*?</span>\s*)+</div>)?"
    r"(?:\s*<div\b[^>]*>\s*(?:<span\b[^>]*>.*?</span>\s*)+</div>)?"
    r"\s*</div>",
    flags=re.IGNORECASE | re.DOTALL,
)

_HIDDEN_BLOCK = re.compile(
    r"<div\s+style=(?P<q>['\"])" + re.escape(_HIDDEN_STYLE) + r"(?P=q)"
    r"\s+aria-hidden=(?P<a>['\"])true(?P=a)>"
    r"\s*(?:<span\b[^>]*>.*?</span>\s*)+"
    r"</div>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ManagedBlockCleanup:
    html: str
    visible_blocks: int
    hidden_blocks: int

    @property
    def changed(self) -> bool:
        return self.visible_blocks > 0 or self.hidden_blocks > 0


def remove_managed_keyword_blocks(body_html: str) -> ManagedBlockCleanup:
    """Remove only app-owned visible/hidden keyword blocks from article HTML."""
    cleaned, visible = _VISIBLE_BLOCK.subn("", body_html or "")
    cleaned, hidden = _HIDDEN_BLOCK.subn("", cleaned)
    return ManagedBlockCleanup(
        html=cleaned.rstrip(),
        visible_blocks=visible,
        hidden_blocks=hidden,
    )


def has_managed_keyword_blocks(body_html: str) -> bool:
    return bool(_VISIBLE_BLOCK.search(body_html or "") or _HIDDEN_BLOCK.search(body_html or ""))

