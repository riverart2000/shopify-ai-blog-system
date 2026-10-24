"""Helpers for Postiz "post published" webhooks.

Postiz does not document (or sign) the payload, so we never depend on its
exact shape: `extract_post_ids` searches the JSON for objects that look like
posts and returns their ids, and the receiver then re-reads those posts
through the API (`list_posts`) instead of trusting the payload.
`payload_fingerprint` gives a stable key for the idempotent webhook inbox.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Keys that mark an object as a post (Postiz `Post` schema and close variants).
POST_MARKERS = frozenset({
    "integration", "integrationId", "publishDate", "content", "releaseURL", "releaseId", "state",
    "group", "parentPostId", "settings",
})
# A child under one of these keys is a post even without markers.
POST_CONTAINER_KEYS = frozenset({"post", "posts", "publishedPost", "published_post"})
# Never descend into these: their `id` belongs to a channel/org/user, not a post.
NON_POST_KEYS = frozenset({"integration", "organization", "user", "customer", "media", "image", "images", "tags"})
_MAX_DEPTH = 12


def _decode(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            return payload
    return payload


def _id(value: Any) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        return text if text else ""
    return ""


def extract_post_ids(payload: Any) -> list[str]:
    """Return the distinct Postiz post ids found anywhere in a webhook payload, in order."""
    found: list[str] = []

    def add(value: Any) -> None:
        post_id = _id(value)
        if post_id and post_id not in found:
            found.append(post_id)

    def walk(node: Any, parent_key: str, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, parent_key, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if "postId" in node:
            add(node["postId"])
        elif "id" in node and (parent_key in POST_CONTAINER_KEYS or POST_MARKERS.intersection(node)):
            add(node["id"])
        for key, value in node.items():
            if key in NON_POST_KEYS or not isinstance(value, (dict, list)):
                continue
            walk(value, key, depth + 1)

    walk(_decode(payload), "", 0)
    return found


def payload_fingerprint(payload: Any) -> str:
    """Stable sha256 of a payload: key order and whitespace do not matter."""
    decoded = _decode(payload)
    if isinstance(decoded, str):
        data = decoded.encode("utf-8")
    else:
        data = json.dumps(decoded, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


__all__ = ["extract_post_ids", "payload_fingerprint"]
