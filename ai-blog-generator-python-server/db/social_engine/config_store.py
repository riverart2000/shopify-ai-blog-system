"""Configuration records: publishing channels, campaigns (auto-pilot rules) and
creative templates."""
from __future__ import annotations

from typing import Any, Iterable

from .connection import connect, dumps, new_id, now, row_to_dict

_CHANNEL_JSON = {"settings_schema": {}, "default_settings": {}, "cadence": {}}
_CAMPAIGN_JSON = {"config": {}, "product_filter": {}, "channel_ids": [], "content": {}, "caption": {}}
_TEMPLATE_JSON = {"formats": [], "spec": {}}


def _channel(row: Any) -> dict | None:
    item = row_to_dict(row, _CHANNEL_JSON)
    if item is not None:
        item["enabled"] = bool(item.get("enabled"))
        item["disabled"] = bool(item.get("disabled"))
    return item


# ---------------------------------------------------------------------------
# Channels (Postiz integrations)
# ---------------------------------------------------------------------------

async def upsert_channel_from_publisher(store_id: str, channel: dict) -> bool:
    """Insert/refresh publisher-owned fields. Returns True for a new channel.

    Local choices (enabled, cadence, default settings) are never overwritten.
    """
    timestamp = now()
    async with connect() as conn:
        async with conn.execute(
            "SELECT 1 FROM social_channels WHERE store_id=? AND channel_id=?",
            (store_id, channel["channel_id"]),
        ) as cur:
            exists = await cur.fetchone() is not None
        await conn.execute(
            """INSERT INTO social_channels
               (store_id, channel_id, provider, name, profile, picture, disabled,
                customer_id, customer_name, last_synced_at, missing_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
               ON CONFLICT(store_id, channel_id) DO UPDATE SET
                 provider=excluded.provider, name=excluded.name, profile=excluded.profile,
                 picture=excluded.picture, disabled=excluded.disabled,
                 customer_id=excluded.customer_id, customer_name=excluded.customer_name,
                 last_synced_at=excluded.last_synced_at, missing_at=NULL""",
            (
                store_id, channel["channel_id"], channel.get("provider", ""), channel.get("name", ""),
                channel.get("profile", ""), channel.get("picture", ""),
                1 if channel.get("disabled") else 0, channel.get("customer_id", ""),
                channel.get("customer_name", ""), timestamp,
            ),
        )
        await conn.commit()
    return not exists


async def set_channel_rules(
    store_id: str, channel_id: str, *, max_length: int | None, rules: str, settings_schema: Any,
) -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE social_channels SET max_length=?, rules=?, settings_schema_json=? WHERE store_id=? AND channel_id=?",
            (max_length, rules or "", dumps(settings_schema or {}), store_id, channel_id),
        )
        await conn.commit()


async def mark_channels_missing(store_id: str, present_ids: Iterable[str]) -> int:
    present = list(present_ids)
    async with connect() as conn:
        if present:
            cur = await conn.execute(
                f"UPDATE social_channels SET missing_at=COALESCE(missing_at, ?) WHERE store_id=? "
                f"AND channel_id NOT IN ({','.join('?' * len(present))})",
                (now(), store_id, *present),
            )
        else:
            cur = await conn.execute(
                "UPDATE social_channels SET missing_at=COALESCE(missing_at, ?) WHERE store_id=?",
                (now(), store_id),
            )
        await conn.commit()
        return cur.rowcount


async def update_channel_preferences(
    store_id: str,
    channel_id: str,
    *,
    enabled: bool | None = None,
    cadence: dict | None = None,
    default_settings: dict | None = None,
) -> bool:
    sets: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        sets.append("enabled=?")
        params.append(1 if enabled else 0)
    if cadence is not None:
        sets.append("cadence_json=?")
        params.append(dumps(cadence))
    if default_settings is not None:
        sets.append("default_settings_json=?")
        params.append(dumps(default_settings))
    if not sets:
        return False
    async with connect() as conn:
        cur = await conn.execute(
            f"UPDATE social_channels SET {', '.join(sets)} WHERE store_id=? AND channel_id=?",
            (*params, store_id, channel_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def get_channel(store_id: str, channel_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_channels WHERE store_id=? AND channel_id=?", (store_id, channel_id),
        ) as cur:
            return _channel(await cur.fetchone())


async def list_channels(store_id: str, *, usable_only: bool = False) -> list[dict]:
    clause = " AND enabled=1 AND disabled=0 AND missing_at IS NULL" if usable_only else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_channels WHERE store_id=?{clause} ORDER BY provider, name",
            (store_id,),
        ) as cur:
            return [_channel(row) for row in await cur.fetchall()]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

def _campaign(row: Any) -> dict | None:
    return row_to_dict(row, _CAMPAIGN_JSON)


async def save_campaign(store_id: str, campaign: dict) -> str:
    campaign_id = campaign.get("id") or new_id("cmp")
    timestamp = now()
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_campaigns
               (id, store_id, name, rule_type, status, config_json, product_filter_json,
                channel_ids_json, content_json, caption_json, approval_mode,
                next_evaluation_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, rule_type=excluded.rule_type, status=excluded.status,
                 config_json=excluded.config_json, product_filter_json=excluded.product_filter_json,
                 channel_ids_json=excluded.channel_ids_json, content_json=excluded.content_json,
                 caption_json=excluded.caption_json, approval_mode=excluded.approval_mode,
                 next_evaluation_at=excluded.next_evaluation_at, updated_at=excluded.updated_at
               WHERE social_campaigns.store_id=excluded.store_id""",
            (
                campaign_id, store_id, campaign["name"], campaign["rule_type"],
                campaign.get("status", "paused"), dumps(campaign.get("config") or {}),
                dumps(campaign.get("product_filter") or {}), dumps(campaign.get("channel_ids") or []),
                dumps(campaign.get("content") or {}), dumps(campaign.get("caption") or {}),
                campaign.get("approval_mode", "manual"),
                campaign.get("next_evaluation_at", timestamp), timestamp, timestamp,
            ),
        )
        await conn.commit()
    return campaign_id


async def get_campaign(store_id: str, campaign_id: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_campaigns WHERE store_id=? AND id=?", (store_id, campaign_id),
        ) as cur:
            return _campaign(await cur.fetchone())


async def list_campaigns(store_id: str, *, statuses: Iterable[str] = ()) -> list[dict]:
    status_list = list(statuses)
    clause = f" AND status IN ({','.join('?' * len(status_list))})" if status_list else ""
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_campaigns WHERE store_id=?{clause} ORDER BY created_at DESC",
            (store_id, *status_list),
        ) as cur:
            return [_campaign(row) for row in await cur.fetchall()]  # type: ignore[misc]


async def due_campaigns(at: int | None = None) -> list[dict]:
    async with connect() as conn:
        async with conn.execute(
            """SELECT * FROM social_campaigns
               WHERE status='active' AND (next_evaluation_at IS NULL OR next_evaluation_at<=?)
               ORDER BY next_evaluation_at ASC""",
            (at or now(),),
        ) as cur:
            return [_campaign(row) for row in await cur.fetchall()]  # type: ignore[misc]


async def mark_campaign_evaluated(campaign_id: str, *, next_at: int | None, error: str = "") -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE social_campaigns SET last_evaluated_at=?, next_evaluation_at=?, last_error=? WHERE id=?",
            (now(), next_at, error[:2000], campaign_id),
        )
        await conn.commit()


async def set_campaign_status(store_id: str, campaign_id: str, status: str) -> bool:
    async with connect() as conn:
        cur = await conn.execute(
            "UPDATE social_campaigns SET status=?, updated_at=?, next_evaluation_at=? WHERE store_id=? AND id=?",
            (status, now(), now() if status == "active" else None, store_id, campaign_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_campaign(store_id: str, campaign_id: str) -> bool:
    async with connect() as conn:
        cur = await conn.execute(
            "DELETE FROM social_campaigns WHERE store_id=? AND id=?", (store_id, campaign_id),
        )
        await conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _template(row: Any) -> dict | None:
    item = row_to_dict(row, _TEMPLATE_JSON)
    if item is not None:
        item["is_builtin"] = bool(item.get("is_builtin"))
        item["is_active"] = bool(item.get("is_active"))
    return item


async def upsert_template(store_id: str, template: dict) -> str:
    template_id = template.get("id") or new_id("tpl")
    timestamp = now()
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO social_templates
               (id, store_id, key, name, content_type, kind, formats_json, spec_json,
                caption_template, is_builtin, is_active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(store_id, key) DO UPDATE SET
                 name=excluded.name, content_type=excluded.content_type, kind=excluded.kind,
                 formats_json=excluded.formats_json, spec_json=excluded.spec_json,
                 caption_template=excluded.caption_template, is_builtin=excluded.is_builtin,
                 updated_at=excluded.updated_at""",
            (
                template_id, store_id, template["key"], template["name"], template["content_type"],
                template.get("kind", "image"), dumps(template.get("formats") or []),
                dumps(template.get("spec") or {}), template.get("caption_template", ""),
                1 if template.get("is_builtin") else 0, 1 if template.get("is_active", True) else 0,
                timestamp, timestamp,
            ),
        )
        await conn.commit()
    return template_id


async def get_template(store_id: str, key: str) -> dict | None:
    async with connect() as conn:
        async with conn.execute(
            "SELECT * FROM social_templates WHERE store_id=? AND key=?", (store_id, key),
        ) as cur:
            return _template(await cur.fetchone())


async def list_templates(store_id: str, *, active_only: bool = False, content_type: str = "") -> list[dict]:
    clauses = ["store_id=?"]
    params: list[Any] = [store_id]
    if active_only:
        clauses.append("is_active=1")
    if content_type:
        clauses.append("content_type=?")
        params.append(content_type)
    async with connect() as conn:
        async with conn.execute(
            f"SELECT * FROM social_templates WHERE {' AND '.join(clauses)} ORDER BY content_type, name",
            params,
        ) as cur:
            return [_template(row) for row in await cur.fetchall()]  # type: ignore[misc]


async def set_template_active(store_id: str, key: str, active: bool) -> bool:
    async with connect() as conn:
        cur = await conn.execute(
            "UPDATE social_templates SET is_active=?, updated_at=? WHERE store_id=? AND key=?",
            (1 if active else 0, now(), store_id, key),
        )
        await conn.commit()
        return cur.rowcount > 0
