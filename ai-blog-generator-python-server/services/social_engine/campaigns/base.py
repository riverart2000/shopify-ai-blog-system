"""Campaign rule contract.

A rule turns a campaign's configuration (+ catalogue, events, history) into
`PlanItem`s. Rules never call the publisher or render anything; the engine
stores the items as planned posts (idempotently, via `dedupe_key`) and the
pipeline generates and publishes them.

Add a rule: create a module in campaigns/rules/, subclass `CampaignRule`,
decorate with `@register_rule`, and import it in campaigns/rules/__init__.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..context import StoreContext
from ..scheduling.slots import ScheduleWindow, SlotPlanner


@dataclass
class PlanItem:
    dedupe_key: str
    content_type: str
    product_ids: list[str]
    channel_ids: list[str]
    channel_times: dict[str, int] = field(default_factory=dict)  # channel_id → epoch seconds
    template_key: str = ""
    title: str = ""
    angle: str = ""
    brief: str = ""
    texts: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def scheduled_at(self) -> int | None:
        return min(self.channel_times.values()) if self.channel_times else None


class ScheduleConfig(BaseModel):
    """Optional per-campaign schedule overrides (falls back to store settings)."""
    model_config = ConfigDict(extra="ignore")
    days: list[int] | None = None
    window_start: str | None = None
    window_end: str | None = None
    randomize: bool | None = None
    use_best_times: bool | None = None

    def window(self, fixed_time: str | None = None) -> ScheduleWindow:
        return ScheduleWindow(
            days=self.days, window_start=self.window_start, window_end=self.window_end,
            randomize=self.randomize, use_best_times=self.use_best_times, fixed_time=fixed_time,
        )


class ProductFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    collection_ids: list[str] = Field(default_factory=list)
    product_ids: list[str] = Field(default_factory=list)
    include_tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    in_stock_only: bool = True
    on_sale_only: bool = False


@dataclass
class RuleContext:
    sctx: StoreContext
    campaign: dict
    config: BaseModel
    product_filter: ProductFilter
    schedule: ScheduleConfig
    channels: list[dict]
    planner: SlotPlanner
    now: datetime
    rng: random.Random
    state: dict[str, Any]                     # persisted per-campaign state (cursors etc.)
    dry_run: bool = False                     # preview: never change stored posts
    simulated_displaced: set[str] = field(default_factory=set)  # dry-run displacements (post ids)

    @property
    def store_id(self) -> str:
        return self.sctx.store_id

    @property
    def campaign_id(self) -> str:
        return str(self.campaign["id"])

    @property
    def channel_ids(self) -> list[str]:
        return [str(c["channel_id"]) for c in self.channels]

    def local_today(self) -> date:
        return self.planner.local_today()

    def planning_days(self, days_ahead: int | None = None) -> list[date]:
        ahead = self.sctx.settings.schedule.plan_days_ahead if days_ahead is None else days_ahead
        today = self.local_today()
        return [today + timedelta(days=offset) for offset in range(0, ahead + 1)]

    def schedule_item(
        self,
        item: PlanItem,
        *,
        day: date | None = None,
        earliest: datetime | None = None,
        latest: datetime | None = None,
        fixed_time: str | None = None,
    ) -> bool:
        """Assign channel times for an item; returns False if nothing fits."""
        groups = self.planner.plan(
            item.channel_ids or self.channel_ids,
            day=day, earliest=earliest, latest=latest, window=self.schedule.window(fixed_time),
        )
        item.channel_times = {cid: int(when.timestamp()) for when, cids in groups for cid in cids}
        item.channel_ids = list(item.channel_times)
        return bool(item.channel_times)

    async def schedule_item_with_priority(self, item: PlanItem, *, day: date, fixed_time: str | None = None) -> bool:
        """Schedule on `day`; if channels are full, take slots from lower-priority
        (SmartQ) posts that haven't been sent to Postiz yet."""
        wanted = list(item.channel_ids or self.channel_ids)
        self.schedule_item(item, day=day, fixed_time=fixed_time)
        missing = [cid for cid in wanted if cid not in item.channel_times]
        if not missing:
            return True
        from .priority import displace_for

        freed = await displace_for(self, day=day, channel_ids=missing, reason=item.title or item.content_type)
        if freed:
            retry = PlanItem(dedupe_key=item.dedupe_key, content_type=item.content_type,
                             product_ids=item.product_ids, channel_ids=missing)
            self.schedule_item(retry, day=day, fixed_time=fixed_time)
            item.channel_times.update(retry.channel_times)
            item.channel_ids = list(item.channel_times)
        return bool(item.channel_times)


class CampaignRule:
    rule_type: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    config_model: ClassVar[type[BaseModel]] = BaseModel
    default_interval_seconds: ClassVar[int] = 3600
    priority: ClassVar[int] = 0          # higher runs first and may take slots from lower (SmartQ = 0)

    async def plan(self, rctx: RuleContext) -> list[PlanItem]:  # pragma: no cover - interface
        raise NotImplementedError

    def next_evaluation(self, rctx: RuleContext) -> int:
        return int(rctx.now.timestamp()) + self.default_interval_seconds


RULES: dict[str, CampaignRule] = {}


def register_rule(cls: type[CampaignRule]) -> type[CampaignRule]:
    if not cls.rule_type:
        raise ValueError(f"{cls.__name__} must define rule_type")
    RULES[cls.rule_type] = cls()
    return cls


def get_rule(rule_type: str) -> CampaignRule | None:
    from . import rules  # noqa: F401  (registers built-in rules)

    return RULES.get(rule_type)


def all_rules() -> dict[str, CampaignRule]:
    from . import rules  # noqa: F401

    return dict(RULES)


def model_defaults(model: type[BaseModel]) -> dict[str, Any]:
    """Defaults for every field that has one (JSON-safe), for UI forms."""
    from pydantic_core import PydanticUndefined

    out: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        if field_info.default_factory is not None:
            value = field_info.default_factory()  # type: ignore[call-arg]
        elif field_info.default is not PydanticUndefined:
            value = field_info.default
        else:
            continue
        out[name] = value.model_dump(mode="json") if isinstance(value, BaseModel) else \
            [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value] if isinstance(value, list) else value
    return out


def rule_catalog() -> dict[str, dict[str, Any]]:
    """Rule metadata for the UI: label, description, JSON schema, defaults, allowed content types."""
    catalog = {}
    for key, rule in all_rules().items():
        allowed = getattr(rule, "allowed_content_types", None)
        catalog[key] = {
            "label": rule.label, "description": rule.description, "priority": rule.priority,
            "schema": rule.config_model.model_json_schema(), "defaults": model_defaults(rule.config_model),
            "allowed_content_types": sorted(allowed) if allowed else None,
        }
    return catalog


def utc_from_epoch(value: int) -> datetime:
    return datetime.fromtimestamp(int(value), tz=timezone.utc)
