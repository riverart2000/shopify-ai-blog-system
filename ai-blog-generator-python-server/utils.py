"""utils.py — Shared utility functions."""
from __future__ import annotations

import json
import logging


def log_debug_payload(log: logging.Logger, label: str, data: object) -> None:
    """Dump *data* as pretty-printed JSON at DEBUG level.

    Image content is replaced with a short summary so logs stay readable:
      - ``bytes`` / ``bytearray``  → ``<binary N bytes>``
      - base64 data-URI strings    → ``<data-URI N chars>``
    Regular image *URL* strings are left intact (they're just text).
    The function is a no-op when DEBUG level is not active, so it is safe
    to call unconditionally in hot paths.
    """
    if not log.isEnabledFor(logging.DEBUG):
        return

    def _sanitise(obj: object) -> object:
        if isinstance(obj, (bytes, bytearray)):
            return f"<binary {len(obj)} bytes>"
        if isinstance(obj, str) and obj.startswith("data:image"):
            return f"<data-URI {len(obj):,} chars>"
        if isinstance(obj, dict):
            return {k: _sanitise(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitise(v) for v in obj]
        return obj

    try:
        log.debug("%s\n%s", label, json.dumps(_sanitise(data), indent=2, default=str))
    except Exception as exc:  # pragma: no cover
        log.debug("%s  <serialisation error: %s>", label, exc)


def text_to_html(text: str) -> str:
    """Convert plain text (## headings, - bullets) to minimal HTML."""
    lines = text.splitlines()
    html_parts: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{stripped[3:].strip()}</h2>")

        elif stripped.startswith("# "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{stripped[2:].strip()}</h2>")

        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{stripped[2:].strip()}</li>")

        elif stripped == "":
            if in_list:
                html_parts.append("</ul>")
                in_list = False

        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{stripped}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)
