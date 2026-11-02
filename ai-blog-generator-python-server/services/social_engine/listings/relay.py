"""Drive the owner's signed-in Chrome through the OpenClaw browser relay.

Each call is one `openclaw browser … --json` subprocess (the relay keeps the
session). Nothing here ever presses a submit button, accepts terms or creates
an account: the relay fills fields and reads pages; the owner clicks.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..errors import ConfigurationError, ExternalServiceError

logger = logging.getLogger("ai_blog_server.social_engine.listings")

_URL_LINE = re.compile(r"^\s*\d+\.\s+(?P<title>.*?)\s+->\s+(?P<url>https?://\S+)\s*$")
_REF_LINE = re.compile(r'^\s*-\s+(?P<role>[A-Za-z]+)\s+"(?P<name>(?:[^"\\]|\\.)*)"(?P<rest>[^\n]*)\[ref=(?P<ref>[A-Za-z0-9_-]+)\]')


@dataclass
class Snapshot:
    url: str = ""
    text: str = ""
    refs: dict[str, dict] = field(default_factory=dict)      # ref -> {role, name, ...}
    links: list[dict] = field(default_factory=list)          # [{title, url}] when captured with urls=True

    def fields(self) -> list[dict]:
        """Interactive elements a form fill can target: [{ref, role, name}]."""
        out = []
        for ref, meta in self.refs.items():
            role = str(meta.get("role") or "").lower()
            if role in {"textbox", "combobox", "searchbox", "spinbutton", "checkbox", "radio", "listbox", "slider"}:
                out.append({"ref": ref, "role": role, "name": str(meta.get("name") or "")})
        return out


def _node_bin_dirs() -> list[str]:
    """nvm installs live outside the service PATH; add them so `npm` resolves."""
    pattern = os.path.expanduser("~/.nvm/versions/node/*/bin")
    return sorted(glob.glob(pattern), reverse=True)


class Relay:
    def __init__(self, relay_dir: str, *, profile: str = "chrome", timeout_seconds: float = 90.0) -> None:
        self.relay_dir = Path(os.path.expanduser(relay_dir or "~/openclaw"))
        self.profile = profile or "chrome"
        self.timeout = float(timeout_seconds)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        extra = [d for d in _node_bin_dirs() if d not in env.get("PATH", "")]
        if extra:
            env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
        return env

    async def run(self, *args: str, timeout: float | None = None) -> Any:
        if not (self.relay_dir / "package.json").is_file():
            raise ConfigurationError(
                f"The Chrome relay folder {self.relay_dir} is missing. Set Social Engine → listings → relay_dir to the OpenClaw checkout.")
        env = self._env()
        npm = shutil.which("npm", path=env["PATH"])
        if not npm:
            raise ConfigurationError("npm was not found; the Chrome relay needs Node (nvm) on this Mac.")
        cmd = [npm, "run", "-s", "openclaw", "--", "browser", "--browser-profile", self.profile, "--json", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(self.relay_dir), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()  # type: ignore[union-attr]
            except Exception:
                pass
            raise ExternalServiceError("chrome_relay", f"The Chrome relay did not answer within {int(timeout or self.timeout)}s ({args[0]}).") from exc
        except OSError as exc:
            raise ExternalServiceError("chrome_relay", f"The Chrome relay could not start: {exc}") from exc
        text = out.decode("utf-8", "replace").strip()
        if proc.returncode != 0 and not text.startswith("{"):
            detail = (err.decode("utf-8", "replace").strip() or text or "no output").splitlines()[-1][:300]
            raise ExternalServiceError("chrome_relay", f"Chrome relay '{args[0]}' failed: {detail}")
        try:
            data = json.loads(text) if text else {}
        except ValueError as exc:
            raise ExternalServiceError("chrome_relay", f"Chrome relay '{args[0]}' returned something that is not JSON: {text[:200]}") from exc
        if isinstance(data, dict) and data.get("ok") is False:
            raise ExternalServiceError("chrome_relay", f"Chrome relay '{args[0]}': {data.get('error') or data.get('message') or 'failed'}")
        return data

    # ------------------------------------------------------------------ tabs

    async def tabs(self) -> list[dict]:
        data = await self.run("tabs", timeout=30)
        return [t for t in (data.get("tabs") or []) if isinstance(t, dict)]

    async def find_tab(self, hosts: tuple[str, ...]) -> dict | None:
        wanted = {h.lower().removeprefix("www.") for h in hosts if h}
        for tab in await self.tabs():
            host = (urlsplit(str(tab.get("url") or "")).hostname or "").lower().removeprefix("www.")
            if host and any(host == w or host.endswith("." + w) for w in wanted):
                return tab
        return None

    async def open(self, url: str) -> str:
        data = await self.run("open", url)
        tab = str(data.get("tabId") or data.get("suggestedTargetId") or data.get("targetId") or "")
        if not tab:
            raise ExternalServiceError("chrome_relay", "The Chrome relay opened the page but did not say which tab it is in.")
        return tab

    async def close(self, tab: str) -> None:
        try:
            await self.run("close", tab, timeout=30)
        except ExternalServiceError:
            pass

    # ------------------------------------------------------------------ reading

    async def snapshot(self, tab: str, *, interactive: bool = True, urls: bool = False, limit: int = 600) -> Snapshot:
        args = ["snapshot", "--target-id", tab, "--limit", str(limit)]
        if interactive:
            args.append("--interactive")
        if urls:
            args.append("--urls")
        data = await self.run(*args)
        text = str(data.get("snapshot") or "")
        refs = {str(k): v for k, v in (data.get("refs") or {}).items() if isinstance(v, dict)}
        if not refs:
            refs = parse_refs(text)
        return Snapshot(url=str(data.get("url") or ""), text=text, refs=refs, links=parse_links(text) if urls else [])

    # ------------------------------------------------------------------ writing (never submits)

    async def fill(self, tab: str, fields: list[dict], *, scratch_dir: Path) -> Any:
        """Fill text fields by ref. `fields`: [{ref, value}]; passed through a file (descriptions are long)."""
        scratch_dir.mkdir(parents=True, exist_ok=True)
        path = scratch_dir / f"fill-{tab}.json"
        payload = [{"ref": f["ref"], "type": "text", "value": str(f["value"])} for f in fields]
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            return await self.run("fill", "--target-id", tab, "--fields-file", str(path))
        finally:
            try:
                path.unlink()
            except OSError:
                pass


def parse_refs(text: str) -> dict[str, dict]:
    refs: dict[str, dict] = {}
    for line in text.splitlines():
        m = _REF_LINE.match(line)
        if m:
            refs[m.group("ref")] = {"role": m.group("role"), "name": m.group("name").replace('\\"', '"')}
    return refs


def parse_links(text: str) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _URL_LINE.match(line)
        if not m:
            continue
        url = m.group("url")
        if url in seen:
            continue
        seen.add(url)
        links.append({"title": m.group("title").strip(), "url": url})
    return links
