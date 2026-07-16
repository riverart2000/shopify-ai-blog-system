"""Shared utilities: logging, HTTP session, HTML->text, slugify."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None  # type: ignore


_LOGGER_NAME = "product_prompts"


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a package logger; configured once by :func:`configure_logging`."""
    return logging.getLogger(_LOGGER_NAME if name is None else f"{_LOGGER_NAME}.{name}")


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False


def build_session(user_agent: str, max_retries: int = 3) -> requests.Session:
    """Create a requests session with retry/backoff and a sane User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    if Retry is not None:
        retry = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


def html_to_text(html: str) -> str:
    """Convert an HTML fragment to clean, readable plain text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned = [ln for ln in lines if ln]
    return "\n".join(cleaned)


def slugify(value: str, max_length: int = 80) -> str:
    """Produce a filesystem-safe slug from arbitrary text."""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s]+", "-", value)
    return value[:max_length].strip("-") or "item"


def first_sentences(text: str, max_chars: int = 320) -> str:
    """Return the leading portion of ``text`` truncated on a word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(".,;:") + "…"
