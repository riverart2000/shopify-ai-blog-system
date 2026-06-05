"""Helpers for timezone-aware scheduled job timing."""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("ai_blog_server")


def normalize_timezone_name(timezone_name: str) -> str:
    return (timezone_name or "").strip() or "UTC"


def is_valid_timezone(timezone_name: str) -> bool:
    try:
        ZoneInfo(normalize_timezone_name(timezone_name))
        return True
    except ZoneInfoNotFoundError:
        return False


def get_next_run_at(
    cron_expr: str,
    timezone_name: str = "UTC",
    *,
    now_utc: dt.datetime | None = None,
) -> int | None:
    """Return the next run timestamp in UTC seconds for a cron expression."""
    tz_name = normalize_timezone_name(timezone_name)

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r for cron %r", tz_name, cron_expr)
        return None

    try:
        if now_utc is None:
            now_utc = dt.datetime.now(dt.timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
        else:
            now_utc = now_utc.astimezone(dt.timezone.utc)

        base_local = now_utc.astimezone(tz)
        next_local = _compute_next_local(cron_expr, base_local)
        return int(next_local.timestamp()) if next_local else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not compute next_run for %r timezone=%r: %s",
            cron_expr,
            tz_name,
            exc,
        )
        return None


def _compute_next_local(cron_expr: str, base_local: dt.datetime) -> dt.datetime | None:
    try:
        from croniter import croniter  # type: ignore

        next_local = croniter(cron_expr, base_local).get_next(dt.datetime)
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=base_local.tzinfo)
        return next_local
    except ModuleNotFoundError:
        return _compute_next_local_fallback(cron_expr, base_local)


def _compute_next_local_fallback(cron_expr: str, base_local: dt.datetime) -> dt.datetime | None:
    parts = cron_expr.split()
    if len(parts) != 5:
        return None

    minute_expr, hour_expr, day_expr, month_expr, weekday_expr = parts
    candidate = base_local.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)

    for _ in range(366 * 24 * 60 * 2):
        cron_weekday = (candidate.weekday() + 1) % 7
        minute_ok = _field_matches(candidate.minute, minute_expr, 0, 59)
        hour_ok = _field_matches(candidate.hour, hour_expr, 0, 23)
        month_ok = _field_matches(candidate.month, month_expr, 1, 12)
        day_ok = _day_matches(candidate.day, cron_weekday, day_expr, weekday_expr)
        if minute_ok and hour_ok and month_ok and day_ok:
            return candidate
        candidate += dt.timedelta(minutes=1)

    return None


def _day_matches(day: int, weekday: int, day_expr: str, weekday_expr: str) -> bool:
    day_any = day_expr == "*"
    weekday_any = weekday_expr == "*"
    day_match = _field_matches(day, day_expr, 1, 31)
    weekday_match = _field_matches(weekday, weekday_expr, 0, 7, sunday_alias=0)

    if day_any and weekday_any:
        return True
    if day_any:
        return weekday_match
    if weekday_any:
        return day_match
    return day_match or weekday_match


def _field_matches(
    value: int,
    expr: str,
    min_value: int,
    max_value: int,
    *,
    sunday_alias: int | None = None,
) -> bool:
    for part in expr.split(","):
        if _part_matches(value, part.strip(), min_value, max_value, sunday_alias=sunday_alias):
            return True
    return False


def _part_matches(
    value: int,
    part: str,
    min_value: int,
    max_value: int,
    *,
    sunday_alias: int | None = None,
) -> bool:
    if not part:
        return False
    if part == "*":
        return True
    if "/" in part:
        base, step_str = part.split("/", 1)
        try:
            step = int(step_str)
        except ValueError:
            return False
        if step <= 0:
            return False
        if base == "*":
            start, end = min_value, max_value
        elif "-" in base:
            start_str, end_str = base.split("-", 1)
            start = _normalize_number(start_str, sunday_alias)
            end = _normalize_number(end_str, sunday_alias)
        else:
            start = _normalize_number(base, sunday_alias)
            end = max_value
        return start <= value <= end and (value - start) % step == 0
    if "-" in part:
        start_str, end_str = part.split("-", 1)
        start = _normalize_number(start_str, sunday_alias)
        end = _normalize_number(end_str, sunday_alias)
        return start <= value <= end
    return value == _normalize_number(part, sunday_alias)


def _normalize_number(raw: str, sunday_alias: int | None) -> int:
    value = int(raw)
    if sunday_alias is not None and value == 7:
        return sunday_alias
    return value