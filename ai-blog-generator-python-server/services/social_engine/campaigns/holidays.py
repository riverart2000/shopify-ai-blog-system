"""Occasion calendar (UK-focused, editable through campaign config).

Dates are computed per year, including movable feasts, so the calendar never
needs yearly maintenance. Custom occasions can be added in a campaign's config
as {"key", "name", "month", "day"}.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Occasion:
    key: str
    name: str
    greeting: str
    theme: str   # promo | wellness | seasonal


def easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


OCCASIONS: dict[str, Occasion] = {o.key: o for o in (
    Occasion("new_year", "New Year", "Here's to a healthier year ahead", "wellness"),
    Occasion("blue_monday", "Blue Monday", "Be kind to yourself today", "wellness"),
    Occasion("valentines", "Valentine's Day", "Self-love counts too", "promo"),
    Occasion("mothers_day_uk", "Mother's Day", "For the women who do it all", "promo"),
    Occasion("easter", "Easter", "A fresh start this spring", "seasonal"),
    Occasion("world_health_day", "World Health Day", "Small habits, big difference", "wellness"),
    Occasion("mental_health_awareness_week", "Mental Health Awareness Week", "Check in with yourself", "wellness"),
    Occasion("fathers_day_uk", "Father's Day", "For the dads who keep going", "promo"),
    Occasion("summer_solstice", "Summer Solstice", "Longest day, brightest energy", "seasonal"),
    Occasion("self_care_day", "International Self-Care Day", "Make time for you", "wellness"),
    Occasion("back_to_school", "Back to Routine", "New season, new routine", "seasonal"),
    Occasion("world_mental_health_day", "World Mental Health Day", "Wellbeing matters", "wellness"),
    Occasion("halloween", "Halloween", "Treat yourself (the healthy way)", "promo"),
    Occasion("singles_day", "Singles' Day", "A treat just for you", "promo"),
    Occasion("black_friday", "Black Friday", "Our biggest offer of the year", "promo"),
    Occasion("cyber_monday", "Cyber Monday", "Last chance for Black Friday prices", "promo"),
    Occasion("christmas", "Christmas", "Wishing you a calm and healthy Christmas", "promo"),
    Occasion("boxing_day", "Boxing Day", "Boxing Day offers", "promo"),
)}


def occasion_date(key: str, year: int) -> date | None:
    if key == "new_year":
        return date(year, 1, 1)
    if key == "blue_monday":
        return _nth_weekday(year, 1, 0, 3)
    if key == "valentines":
        return date(year, 2, 14)
    if key == "mothers_day_uk":
        return easter_sunday(year) - timedelta(days=21)
    if key == "easter":
        return easter_sunday(year)
    if key == "world_health_day":
        return date(year, 4, 7)
    if key == "mental_health_awareness_week":
        return _nth_weekday(year, 5, 0, 2)
    if key == "fathers_day_uk":
        return _nth_weekday(year, 6, 6, 3)
    if key == "summer_solstice":
        return date(year, 6, 21)
    if key == "self_care_day":
        return date(year, 7, 24)
    if key == "back_to_school":
        return _nth_weekday(year, 9, 0, 1)
    if key == "world_mental_health_day":
        return date(year, 10, 10)
    if key == "halloween":
        return date(year, 10, 31)
    if key == "singles_day":
        return date(year, 11, 11)
    if key == "black_friday":
        return _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
    if key == "cyber_monday":
        return _nth_weekday(year, 11, 3, 4) + timedelta(days=4)
    if key == "christmas":
        return date(year, 12, 25)
    if key == "boxing_day":
        return date(year, 12, 26)
    return None


def upcoming_occasions(
    start: date,
    end: date,
    *,
    keys: list[str] | None = None,
    custom: list[dict] | None = None,
) -> list[tuple[date, Occasion]]:
    """Occasions falling within [start, end], sorted by date. `keys=None` means all."""
    selected = list(OCCASIONS) if keys is None else keys
    found: list[tuple[date, Occasion]] = []
    for year in range(start.year, end.year + 1):
        for key in selected:
            occasion = OCCASIONS.get(key)
            when = occasion_date(key, year)
            if occasion and when and start <= when <= end:
                found.append((when, occasion))
        for item in custom or []:
            try:
                when = date(year, int(item["month"]), int(item["day"]))
            except (KeyError, ValueError, TypeError):
                continue
            if start <= when <= end:
                found.append((when, Occasion(
                    str(item.get("key") or item.get("name", "custom")),
                    str(item.get("name", "Occasion")),
                    str(item.get("greeting", "")), str(item.get("theme", "promo")),
                )))
    return sorted(found, key=lambda pair: pair[0])
