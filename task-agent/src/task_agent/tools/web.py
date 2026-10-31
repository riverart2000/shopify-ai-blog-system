"""Read-only web tools: search and fetch a page as plain text."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from task_agent.guardrails import wrap_untrusted

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
TEXT_TYPES = ("text/html", "application/xhtml", "text/plain", "application/json", "text/xml", "application/xml")
MAX_REDIRECTS = 5
MAX_BYTES = 3_000_000


class BlockedURL(ValueError):
    """The URL points somewhere the agent must not go (local network, non-web scheme)."""


async def check_public_url(url: str) -> None:
    """Allow only http(s) URLs whose host resolves to public internet addresses.

    Stops page content from steering the agent at this Mac's own services (oMLX admin, the live server)
    or anything else on the local network or Tailscale.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"Only http(s) links are allowed, not '{parts.scheme or 'none'}'.")
    host = parts.hostname
    if not host:
        raise BlockedURL("The link has no host name.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURL(f"Couldn't find the site '{host}'.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise BlockedURL(f"'{host}' points to a private or local address; blocked.")


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable text) from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template", "nav", "footer", "form"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    text = "\n".join(line for line in lines if line)
    return title, text


async def fetch_text(url: str, max_chars: int, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Fetch a public page. Returns (final URL after redirects, text wrapped as untrusted data)."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await check_public_url(current)
            response = await client.get(current, follow_redirects=False)
            if response.is_redirect and "location" in response.headers:
                current = urljoin(current, response.headers["location"])
                continue
            break
        else:
            raise BlockedURL("Too many redirects.")
    finally:
        if own_client:
            await client.aclose()

    if response.status_code >= 400:
        return current, wrap_untrusted(current, f"[HTTP {response.status_code} — the page could not be loaded]")
    content_type = response.headers.get("content-type", "").lower()
    if not any(t in content_type for t in TEXT_TYPES):
        return current, wrap_untrusted(current, f"[Not a text page ({content_type or 'unknown type'}); skipped]")

    body = response.content[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")
    if "html" in content_type:
        title, text = html_to_text(body)
        text = f"Title: {title}\n\n{text}" if title else text
    else:
        text = body
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[... trimmed, page has {len(text):,} characters]"
    return current, wrap_untrusted(current, text)


async def search(query: str, max_results: int = 8) -> str:
    """Web search via DuckDuckGo (no API key). Results are wrapped as untrusted data."""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        results = await asyncio.to_thread(lambda: DDGS().text(query, max_results=max_results))
    except DDGSException as exc:
        # An empty or failed search is a normal result for the agent, not a job failure.
        if "no results" in str(exc).lower():
            return wrap_untrusted(f"search: {query}", "[No results]")
        return f"[Search failed ({type(exc).__name__}). Try a different query.]"
    if not results:
        return wrap_untrusted(f"search: {query}", "[No results]")
    lines = [
        f"{i}. {r.get('title', '').strip()}\n   {r.get('href', '')}\n   {r.get('body', '').strip()}"
        for i, r in enumerate(results, 1)
    ]
    return wrap_untrusted(f"search: {query}", "\n".join(lines))
