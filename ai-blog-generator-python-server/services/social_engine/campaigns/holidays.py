"""Occasion calendar for the UK and the US (editable through campaign config).

Each occasion says where it's celebrated (`countries`): UK only (Boxing Day, UK Mother's Day, bank holidays),
US only (Thanksgiving, 4th of July, …) or both (Black Friday, Christmas, festivals, wellness days). That sets
the promotion's market (task: UK time and £, US time and $, or both).

Dates are computed per year, including movable feasts. Festivals on lunar calendars (Lunar New Year, Holi,
Eid, Diwali, Hanukkah) come from FESTIVAL_DATES, checked against published calendars in Nov 2026; extend the
table before 2031 (a festival with no date that year is simply skipped). Custom occasions can be added in a
campaign's config as {"key", "name", "month", "day"}.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


UK, US, BOTH = ("GB",), ("US",), ("GB", "US")


@dataclass(frozen=True)
class Occasion:
    key: str
    name: str
    greeting: str
    theme: str   # promo | wellness | seasonal | festival
    countries: tuple[str, ...] = BOTH


# Lunar-calendar festivals (main day; Hanukkah = the first night). Sources: hebcal.com, publicholidays.com,
# calendardate.com, chinahighlights.com (checked 4 Nov 2026). Moon-sighting can move Eid by a day.
FESTIVAL_DATES: dict[str, dict[int, tuple[int, int]]] = {
    "lunar_new_year": {2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
                       2031: (1, 23), 2032: (2, 11), 2033: (1, 31), 2034: (2, 19), 2035: (2, 8)},
    "holi": {2026: (3, 4), 2027: (3, 22), 2028: (3, 11), 2029: (2, 28), 2030: (3, 20)},
    "eid_al_fitr": {2026: (3, 20), 2027: (3, 10), 2028: (2, 27), 2029: (2, 14), 2030: (2, 4)},
    "eid_al_adha": {2026: (5, 27), 2027: (5, 16), 2028: (5, 5), 2029: (4, 24), 2030: (4, 13)},
    "diwali": {2026: (11, 8), 2027: (10, 29), 2028: (10, 17), 2029: (11, 5), 2030: (10, 26)},
    "hanukkah": {2026: (12, 4), 2027: (12, 24), 2028: (12, 12), 2029: (12, 1), 2030: (12, 20)},
}  # fmt: skip


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
    # Shared by the UK and the US
    Occasion("new_year", "New Year", "Here's to a healthier year ahead", "wellness"),
    Occasion("blue_monday", "Blue Monday", "Be kind to yourself today", "wellness"),
    Occasion("valentines", "Valentine's Day", "Self-love counts too", "promo"),
    Occasion("easter", "Easter", "A fresh start this spring", "seasonal"),
    Occasion("world_health_day", "World Health Day", "Small habits, big difference", "wellness"),
    Occasion("fathers_day_uk", "Father's Day", "For the dads who keep going", "promo"),  # same Sunday in both
    Occasion("global_wellness_day", "Global Wellness Day", "One day can change your life", "wellness"),
    Occasion("summer_solstice", "Summer Solstice", "Longest day, brightest energy", "seasonal"),
    Occasion("self_care_day", "International Self-Care Day", "Make time for you", "wellness"),
    Occasion("back_to_school", "Back to Routine", "New season, new routine", "seasonal"),
    Occasion("world_mental_health_day", "World Mental Health Day", "Wellbeing matters", "wellness"),
    Occasion("halloween", "Halloween", "Treat yourself (the healthy way)", "promo"),
    Occasion("singles_day", "Singles' Day", "A treat just for you", "promo"),
    Occasion("black_friday", "Black Friday", "Our biggest offer of the year", "promo"),
    Occasion("cyber_monday", "Cyber Monday", "Last chance for Black Friday prices", "promo"),
    Occasion("world_meditation_day", "World Meditation Day", "A few quiet minutes", "wellness"),
    Occasion("christmas", "Christmas", "Wishing you a calm and healthy Christmas", "promo"),
    # Festivals (both countries)
    Occasion("lunar_new_year", "Lunar New Year", "Wishing you health in the new year", "festival"),
    Occasion("holi", "Holi", "A colourful, joyful Holi", "festival"),
    Occasion("st_patricks", "St Patrick's Day", "Happy St Patrick's Day", "festival"),
    Occasion("eid_al_fitr", "Eid al-Fitr", "Eid Mubarak", "festival"),
    Occasion("eid_al_adha", "Eid al-Adha", "Eid Mubarak", "festival"),
    Occasion("diwali", "Diwali", "Wishing you a bright Diwali", "festival"),
    Occasion("hanukkah", "Hanukkah", "Happy Hanukkah", "festival"),
    # UK only
    Occasion("mothers_day_uk", "Mother's Day (UK)", "For the women who do it all", "promo", UK),
    Occasion("mental_health_awareness_week", "Mental Health Awareness Week", "Check in with yourself", "wellness",
             UK),
    Occasion("early_may_bank_holiday", "Early May Bank Holiday", "Enjoy the long weekend", "promo", UK),
    Occasion("spring_bank_holiday", "Spring Bank Holiday", "Enjoy the long weekend", "promo", UK),
    Occasion("summer_bank_holiday", "Summer Bank Holiday", "Enjoy the long weekend", "promo", UK),
    Occasion("boxing_day", "Boxing Day", "Boxing Day offers", "promo", UK),
    # US only
    Occasion("presidents_day", "Presidents' Day", "Presidents' Day savings", "promo", US),
    Occasion("mothers_day_us", "Mother's Day (US)", "For the moms who do it all", "promo", US),
    Occasion("memorial_day", "Memorial Day", "Memorial Day weekend", "promo", US),
    Occasion("independence_day", "4th of July", "Happy 4th of July", "promo", US),
    Occasion("labor_day", "Labor Day", "Labor Day weekend", "promo", US),
    Occasion("thanksgiving", "Thanksgiving", "Grateful for you", "promo", US),
    Occasion("small_business_saturday", "Small Business Saturday", "Thank you for shopping small", "promo", US),
)}


def _thanksgiving(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4)


_FIXED: dict[str, tuple[int, int]] = {
    "new_year": (1, 1), "valentines": (2, 14), "st_patricks": (3, 17), "world_health_day": (4, 7),
    "summer_solstice": (6, 21), "independence_day": (7, 4), "self_care_day": (7, 24),
    "world_mental_health_day": (10, 10), "halloween": (10, 31), "singles_day": (11, 11),
    "world_meditation_day": (12, 21), "christmas": (12, 25), "boxing_day": (12, 26),
}


def occasion_date(key: str, year: int) -> date | None:
    if key in _FIXED:
        month, day = _FIXED[key]
        return date(year, month, day)
    if key in FESTIVAL_DATES:
        found = FESTIVAL_DATES[key].get(year)
        return date(year, *found) if found else None
    if key == "blue_monday":
        return _nth_weekday(year, 1, 0, 3)
    if key == "presidents_day":
        return _nth_weekday(year, 2, 0, 3)
    if key == "mothers_day_uk":
        return easter_sunday(year) - timedelta(days=21)
    if key == "easter":
        return easter_sunday(year)
    if key == "early_may_bank_holiday":
        return _nth_weekday(year, 5, 0, 1)
    if key == "mothers_day_us":
        return _nth_weekday(year, 5, 6, 2)
    if key == "mental_health_awareness_week":
        return _nth_weekday(year, 5, 0, 2)
    if key in ("spring_bank_holiday", "memorial_day"):
        return _last_weekday(year, 5, 0)
    if key == "global_wellness_day":
        return _nth_weekday(year, 6, 5, 2)
    if key == "fathers_day_uk":
        return _nth_weekday(year, 6, 6, 3)
    if key == "summer_bank_holiday":
        return _last_weekday(year, 8, 0)
    if key in ("back_to_school", "labor_day"):
        return _nth_weekday(year, 9, 0, 1)
    if key == "thanksgiving":
        return _thanksgiving(year)
    if key == "black_friday":
        return _thanksgiving(year) + timedelta(days=1)
    if key == "small_business_saturday":
        return _thanksgiving(year) + timedelta(days=2)
    if key == "cyber_monday":
        return _thanksgiving(year) + timedelta(days=4)
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
