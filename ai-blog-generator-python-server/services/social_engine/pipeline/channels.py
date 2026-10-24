"""Channel sync (Postiz integrations → social_channels) and Postiz notifications."""
from __future__ import annotations

import logging
from typing import Any

from db.social_engine import config_store, observability

from ..activity import record
from ..context import load_store_context
from ..errors import SocialEngineError

logger = logging.getLogger("ai_blog_server.social_engine.pipeline")


async def sync_channels(store_id: str, *, with_rules: bool = True) -> dict[str, Any]:
    from ..publishers import get_publisher

    sctx = await load_store_context(store_id)
    publisher = get_publisher(sctx, correlation_id=f"channels:{store_id}")
    try:
        remote = await publisher.list_channels()
        if sctx.settings.postiz.customer_id:
            remote = [c for c in remote if c.customer_id == sctx.settings.postiz.customer_id]
        before = {c["channel_id"]: c for c in await config_store.list_channels(store_id)}
        new, disabled, rule_errors = [], [], []
        for channel in remote:
            is_new = await config_store.upsert_channel_from_publisher(store_id, {
                "channel_id": channel.channel_id, "provider": channel.provider, "name": channel.name,
                "profile": channel.profile, "picture": channel.picture, "disabled": channel.disabled,
                "customer_id": channel.customer_id, "customer_name": channel.customer_name,
            })
            if is_new:
                new.append(f"{channel.name} ({channel.provider})")
            if channel.disabled and not before.get(channel.channel_id, {}).get("disabled"):
                disabled.append(f"{channel.name} ({channel.provider})")
            if with_rules and not channel.disabled:
                try:
                    rules = await publisher.channel_rules(channel.channel_id)
                    await config_store.set_channel_rules(
                        store_id, channel.channel_id, max_length=rules.max_length, rules=rules.rules,
                        settings_schema=rules.settings_schema,
                    )
                except SocialEngineError as exc:
                    rule_errors.append(f"{channel.name}: {exc.message}")
        missing = await config_store.mark_channels_missing(store_id, [c.channel_id for c in remote])
        boards_set = await _default_pinterest_boards(store_id, sctx, publisher)
    finally:
        await publisher.aclose()

    if new:
        await record(store_id, "channels", store_id, f"New channel(s) connected: {', '.join(new)}", stage="sync")
    if disabled or missing:
        await record(store_id, "channels", store_id,
                     "Channel(s) need attention in Postiz: "
                     + ", ".join(disabled + ([f"{missing} removed"] if missing else [])),
                     level="warning", stage="sync")
    await observability.set_state(f"channels:last_sync:{store_id}", {"count": len(remote), "errors": rule_errors})
    return {"channels": len(remote), "new": new, "disabled": disabled, "missing": missing,
            "rule_errors": rule_errors, "pinterest_boards_set": boards_set}


def _normalise(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def pick_board(boards: list[dict], brand_names: list[str]) -> dict | None:
    """Choose the board named like the brand (e.g. "Bioluxelab"), else the first board."""
    if not boards:
        return None
    wanted = [_normalise(name) for name in brand_names if _normalise(name)]
    for board in boards:
        if _normalise(board.get("name", "")) in wanted:
            return board
    for board in boards:
        name = _normalise(board.get("name", ""))
        if any(w and (w in name or name in w) for w in wanted):
            return board
    return boards[0]


async def _default_pinterest_boards(store_id: str, sctx, publisher) -> list[str]:
    """Pinterest needs a board on every pin: give new Pinterest channels a sensible default."""
    chosen: list[str] = []
    for channel in await config_store.list_channels(store_id, usable_only=True):
        settings = channel.get("default_settings") or {}
        if channel["provider"] != "pinterest" or settings.get("board"):
            continue
        try:
            raw = await publisher.run_channel_tool(channel["channel_id"], "boards", {})
        except SocialEngineError as exc:
            logger.warning("Could not list Pinterest boards for %s: %s", channel["name"], exc.message,
                           extra={"store_id": store_id})
            continue
        boards = raw if isinstance(raw, list) else (raw or {}).get("output") or (raw or {}).get("boards") or []
        boards = [b for b in boards if isinstance(b, dict) and (b.get("id") or b.get("value"))]
        brand = sctx.settings.brand
        board = pick_board(boards, [brand.business_name, sctx.store.get("name", ""),
                                    brand.website.replace("https://", "").split(".")[0]])
        if not board:
            continue
        board_id = str(board.get("id") or board.get("value"))
        await config_store.update_channel_preferences(
            store_id, channel["channel_id"],
            default_settings={**settings, "board": board_id, "board_name": board.get("name", "")},
        )
        chosen.append(f"{channel['name']} → {board.get('name', board_id)}")
        await record(store_id, "channels", channel["channel_id"],
                     f"Pinterest board set to '{board.get('name', board_id)}' (change it on the Channels page)",
                     stage="sync")
    return chosen


async def poll_notifications(store_id: str) -> dict[str, Any]:
    """Surface Postiz notifications (failed posts, disconnected channels) once each."""
    from ..publishers import get_publisher

    sctx = await load_store_context(store_id)
    publisher = get_publisher(sctx, correlation_id=f"notifications:{store_id}")
    key = f"notifications:seen:{store_id}"
    seen = set(await observability.get_state(key, []) or [])
    fresh = 0
    try:
        items = await publisher.notifications(page=0)
    finally:
        await publisher.aclose()
    for item in items:
        if not item.external_id or item.external_id in seen:
            continue
        seen.add(item.external_id)
        fresh += 1
        await record(store_id, "postiz", item.external_id, f"Postiz: {item.content}",
                     level="error" if item.level == "error" else "warning" if item.level == "warning" else "info",
                     stage="notification", data={"link": item.link})
    await observability.set_state(key, sorted(seen)[-500:])
    return {"new": fresh, "total": len(items)}
