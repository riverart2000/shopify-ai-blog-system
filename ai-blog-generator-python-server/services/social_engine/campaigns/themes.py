"""Promotion themes (Outfy PromoQ "Occasions", "Offers & Specials", "New Arrivals").

Each theme bundles everything a promotion needs so creating one is a single
click: promo lines (rotated at random), a seasonal palette, sticker/badge
text, a recommended start (days before the date) and duration, the content
mix, and a prompt for a Postiz AI background scene (no text, no product —
our real product photo is composited on top).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from .holidays import OCCASIONS, occasion_date

PALETTES: dict[str, dict[str, str]] = {
    "brand": {},
    "black_friday": {"primary": "#111111", "text": "#f5f1e8", "background": "#0b0b0b", "cta": "#c9a227",
                     "accent": "#3a3a3a", "secondary": "#1d1d1d"},
    "christmas": {"primary": "#7a1f2b", "text": "#1d2a1f", "background": "#f6f1e7", "cta": "#b89245",
                  "accent": "#2f5d3a", "secondary": "#4d1219"},
    "valentines": {"primary": "#b3364b", "text": "#3a1820", "background": "#fbeff1", "cta": "#b3364b",
                   "accent": "#f2c6cf", "secondary": "#7d2233"},
    "mothers_day": {"primary": "#c27a8a", "text": "#3b2a2e", "background": "#fbf3ef", "cta": "#a35d6d",
                    "accent": "#ecd5cf", "secondary": "#8c5361"},
    "fathers_day": {"primary": "#24425f", "text": "#1b2733", "background": "#f2f0eb", "cta": "#b89245",
                    "accent": "#c9d4de", "secondary": "#16293c"},
    "halloween": {"primary": "#d4661c", "text": "#f6efe6", "background": "#1a1410", "cta": "#d4661c",
                  "accent": "#4a3628", "secondary": "#2b1f17"},
    "summer": {"primary": "#e0714f", "text": "#20343d", "background": "#fdf6ec", "cta": "#e0714f",
               "accent": "#9fd3d0", "secondary": "#2f6f7a"},
    "spring": {"primary": "#6a8f4e", "text": "#22301a", "background": "#f6f8ef", "cta": "#b89245",
               "accent": "#d8e5c4", "secondary": "#4c6a37"},
    "new_year": {"primary": "#1c2b4a", "text": "#f5f1e8", "background": "#101a30", "cta": "#c9a227",
                 "accent": "#2c3e66", "secondary": "#0b1222"},
    "calm": {"primary": "#4f6d7a", "text": "#1f2c33", "background": "#f2f5f4", "cta": "#285746",
             "accent": "#c9d8d6", "secondary": "#344b55"},
}


@dataclass(frozen=True)
class PromotionTheme:
    key: str
    label: str
    category: str                      # occasion | offer | new_arrivals | custom
    occasion_key: str = ""             # date from the occasion calendar
    emoji: str = ""
    promo_texts: tuple[str, ...] = ()
    palette: str = "brand"
    sticker: str = ""
    lead_days: int = 7                 # start this many days before the occasion
    duration_days: int = 7
    content_types: tuple[str, ...] = ("sale_promo", "product_video")
    background_prompt: str = ""
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def market(self) -> str:
        """Who an occasion's promotion is for: "GB", "US" or "both" (offers and new arrivals: "GB")."""
        from ..markets import market_for

        occasion = OCCASIONS.get(self.occasion_key)
        return market_for(occasion.countries) if occasion else "GB"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["colors"] = PALETTES.get(self.palette, {})
        data["market"] = self.market
        return data


_BG = ("Photorealistic, softly lit lifestyle scene, empty space in the centre for a product, "
       "no people, no text, no logos, no packaging, shallow depth of field, premium wellness brand look, ")

THEMES: dict[str, PromotionTheme] = {t.key: t for t in (
    # ---------------------------------------------------------------- occasions
    PromotionTheme("black_friday", "Black Friday", "occasion", "black_friday", "🖤",
                   ("Black Friday: our best offer", "The Black Friday edit", "Biggest savings of the year",
                    "Black Friday starts now", "Treat yourself for less"),
                   "black_friday", "Black Friday", lead_days=7, duration_days=5,
                   background_prompt=_BG + "dramatic black marble surface with warm gold rim light, luxurious mood"),
    PromotionTheme("cyber_monday", "Cyber Monday", "occasion", "cyber_monday", "💻",
                   ("Cyber Monday: last chance", "Final hours of our sale", "Cyber Monday savings"),
                   "black_friday", "Cyber Monday", lead_days=0, duration_days=2,
                   background_prompt=_BG + "dark modern desk with cool blue and gold accent light"),
    PromotionTheme("christmas", "Christmas", "occasion", "christmas", "🎄",
                   ("Wellness gifts they'll love", "The Christmas gift edit", "Give the gift of wellbeing",
                    "Festive favourites", "Order in time for Christmas"),
                   "christmas", "Christmas", lead_days=21, duration_days=15,
                   background_prompt=_BG + "cosy festive table with pine sprigs, warm fairy lights bokeh, linen"),
    PromotionTheme("boxing_day", "Boxing Day", "occasion", "boxing_day", "🎁",
                   ("Boxing Day offers", "Post-Christmas treats", "Start the new year well"),
                   "christmas", "Boxing Day", lead_days=0, duration_days=3,
                   background_prompt=_BG + "calm winter morning light on a wooden table with wrapping paper"),
    PromotionTheme("new_year", "New Year", "occasion", "new_year", "✨",
                   ("New year, new routine", "Start the year well", "Your healthiest year yet"),
                   "new_year", "New Year", lead_days=5, duration_days=10,
                   background_prompt=_BG + "midnight blue backdrop with soft golden sparkle bokeh"),
    PromotionTheme("valentines", "Valentine's Day", "occasion", "valentines", "❤️",
                   ("Self-love counts too", "Gifts for someone special", "Love your routine"),
                   "valentines", "Valentine's", lead_days=10, duration_days=10,
                   background_prompt=_BG + "soft blush pink satin with scattered rose petals"),
    PromotionTheme("mothers_day", "Mother's Day (UK)", "occasion", "mothers_day_uk", "💐",
                   ("For the women who do it all", "Mother's Day gifts", "Treat Mum to some me-time"),
                   "mothers_day", "Mother's Day", lead_days=14, duration_days=14,
                   background_prompt=_BG + "fresh spring flowers and soft morning light on a cream table"),
    PromotionTheme("easter", "Easter", "occasion", "easter", "🌷",
                   ("Spring into a fresh routine", "Easter wellness treats", "A fresh start this spring"),
                   "spring", "Easter", lead_days=7, duration_days=7,
                   background_prompt=_BG + "spring tulips and pastel light on a light oak table"),
    PromotionTheme("fathers_day", "Father's Day", "occasion", "fathers_day_uk", "👔",
                   ("For the dads who keep going", "Father's Day gifts", "Gifts for active dads"),
                   "fathers_day", "Father's Day", lead_days=10, duration_days=10,
                   background_prompt=_BG + "deep navy fabric, leather and wood textures, morning light"),
    PromotionTheme("summer_sale", "Summer Sale", "occasion", "summer_solstice", "☀️",
                   ("Summer sale is on", "Sunshine savings", "Your summer routine, for less"),
                   "summer", "Summer Sale", lead_days=3, duration_days=14,
                   background_prompt=_BG + "sunlit terrace with palm leaf shadows and turquoise water bokeh"),
    PromotionTheme("back_to_routine", "Back to Routine", "occasion", "back_to_school", "📚",
                   ("New season, new routine", "Back to your best", "September reset"),
                   "calm", "Back to Routine", lead_days=5, duration_days=10,
                   background_prompt=_BG + "tidy desk with notebook and autumn morning light"),
    PromotionTheme("halloween", "Halloween", "occasion", "halloween", "🎃",
                   ("Treat yourself (the healthy way)", "No tricks, just treats", "Spooky-good savings"),
                   "halloween", "Halloween", lead_days=7, duration_days=7,
                   background_prompt=_BG + "moody autumn scene with small pumpkins and candle glow"),
    PromotionTheme("singles_day", "Singles' Day", "occasion", "singles_day", "🛍️",
                   ("A treat just for you", "Singles' Day savings", "11.11 offers"),
                   "brand", "11.11", lead_days=2, duration_days=3,
                   background_prompt=_BG + "minimal studio with soft gradient light"),
    PromotionTheme("blue_monday", "Blue Monday", "occasion", "blue_monday", "💙",
                   ("Be kind to yourself today", "Beat the January blues", "Small comforts, big difference"),
                   "calm", "Blue Monday", lead_days=2, duration_days=3, content_types=("holiday", "sale_promo"),
                   background_prompt=_BG + "cosy blanket, warm tea and soft winter window light"),
    PromotionTheme("mental_health_week", "Mental Health Awareness Week", "occasion",
                   "mental_health_awareness_week", "🌿",
                   ("Check in with yourself", "Small habits, calmer days", "Time for you"),
                   "calm", "Wellbeing Week", lead_days=1, duration_days=7, content_types=("holiday", "sale_promo"),
                   extra={"code": "WELLBEINGWEEK"},
                   background_prompt=_BG + "tranquil botanical scene with soft green light"),
    PromotionTheme("self_care_day", "International Self-Care Day", "occasion", "self_care_day", "🧘",
                   ("Make time for you", "Self-care, made simple", "Your self-care edit"),
                   "calm", "Self-Care Day", lead_days=3, duration_days=4, content_types=("holiday", "sale_promo"),
                   extra={"code": "SELFCARE"},
                   background_prompt=_BG + "spa-like bathroom shelf with eucalyptus and soft daylight"),
    PromotionTheme("world_mental_health_day", "World Mental Health Day", "occasion", "world_mental_health_day", "💚",
                   ("Wellbeing matters", "Look after your mind", "Small steps count"),
                   "calm", "Wellbeing Day", lead_days=1, duration_days=2, content_types=("holiday", "sale_promo"),
                   extra={"code": "WELLBEING"},
                   background_prompt=_BG + "calm forest light through leaves"),
    # ------------------------------------------------- more wellness days (greeting + sale)
    PromotionTheme("world_health_day", "World Health Day", "occasion", "world_health_day", "🍏",
                   ("Small habits, big difference", "Invest in your everyday health", "Healthy habits, made easy"),
                   "calm", "World Health Day", lead_days=1, duration_days=2, content_types=("holiday", "sale_promo"),
                   extra={"code": "HEALTHDAY"},
                   background_prompt=_BG + "fresh green apples, water glass and soft morning light"),
    PromotionTheme("global_wellness_day", "Global Wellness Day", "occasion", "global_wellness_day", "🌞",
                   ("One day can change your life", "Your wellness reset", "Feel-good routines for less"),
                   "calm", "Wellness Day", lead_days=1, duration_days=2, content_types=("holiday", "sale_promo"),
                   extra={"code": "WELLNESSDAY"},
                   background_prompt=_BG + "sunny garden table with herbs and soft natural light"),
    PromotionTheme("world_meditation_day", "World Meditation Day", "occasion", "world_meditation_day", "🕯️",
                   ("A few quiet minutes", "Make space to breathe", "Your calm corner, for less"),
                   "calm", "Meditation Day", lead_days=1, duration_days=2, content_types=("holiday", "sale_promo"),
                   extra={"code": "MEDITATION"},
                   background_prompt=_BG + "candle, cushion and soft winter light in a calm room"),
    # ---------------------------------------------------------- festivals (UK + US)
    PromotionTheme("lunar_new_year", "Lunar New Year", "occasion", "lunar_new_year", "🧧",
                   ("Wishing you health in the new year", "Lunar New Year treats", "A fresh start, for less"),
                   "christmas", "Lunar New Year", lead_days=5, duration_days=7, content_types=("holiday", "sale_promo"),
                   background_prompt=_BG + "red and gold paper lanterns softly blurred, warm festive light"),
    PromotionTheme("holi", "Holi", "occasion", "holi", "🌈",
                   ("A colourful, joyful Holi", "Celebrate with colour", "Holi treats for you"),
                   "summer", "Holi", lead_days=3, duration_days=4, content_types=("holiday", "sale_promo"),
                   background_prompt=_BG + "soft clouds of pastel coloured powder in bright daylight"),
    PromotionTheme("st_patricks", "St Patrick's Day", "occasion", "st_patricks", "☘️",
                   ("Happy St Patrick's Day", "Lucky savings", "A little luck for your routine"),
                   "spring", "St Patrick's", lead_days=3, duration_days=4,
                   extra={"code": "LUCKY"},
                   background_prompt=_BG + "fresh green clover leaves on light wood, spring light"),
    PromotionTheme("eid_al_fitr", "Eid al-Fitr", "occasion", "eid_al_fitr", "🌙",
                   ("Eid Mubarak", "Gifts for Eid", "Celebrate Eid with wellbeing"),
                   "new_year", "Eid Mubarak", lead_days=5, duration_days=7, content_types=("holiday", "sale_promo"),
                   extra={"code": "EID"},
                   background_prompt=_BG + "crescent-moon lanterns and warm golden light, deep blue evening"),
    PromotionTheme("eid_al_adha", "Eid al-Adha", "occasion", "eid_al_adha", "🌙",
                   ("Eid Mubarak", "Gifts for Eid", "Celebrate Eid with wellbeing"),
                   "new_year", "Eid Mubarak", lead_days=5, duration_days=7, content_types=("holiday", "sale_promo"),
                   extra={"code": "EIDADHA"},
                   background_prompt=_BG + "lanterns and warm golden light on a deep blue evening table"),
    PromotionTheme("diwali", "Diwali", "occasion", "diwali", "🪔",
                   ("Wishing you a bright Diwali", "Diwali gifts for wellbeing", "Light up your routine"),
                   "christmas", "Diwali", lead_days=5, duration_days=7, content_types=("holiday", "sale_promo"),
                   background_prompt=_BG + "glowing diya oil lamps and marigold petals, warm evening light"),
    PromotionTheme("hanukkah", "Hanukkah", "occasion", "hanukkah", "🕎",
                   ("Happy Hanukkah", "Eight nights of gifts", "Gifts of wellbeing"),
                   "new_year", "Hanukkah", lead_days=3, duration_days=8, content_types=("holiday", "sale_promo"),
                   background_prompt=_BG + "soft candlelight and blue and silver ribbon, winter evening"),
    # ------------------------------------------------------------------- UK only
    PromotionTheme("bank_holiday_may", "Early May Bank Holiday", "occasion", "early_may_bank_holiday", "🌼",
                   ("Long weekend savings", "Bank holiday treats", "Make the most of the long weekend"),
                   "spring", "Bank Holiday", lead_days=3, duration_days=4, extra={"code": "BANKHOLIDAY"},
                   background_prompt=_BG + "spring blossom and bright morning light on a garden table"),
    PromotionTheme("bank_holiday_spring", "Spring Bank Holiday", "occasion", "spring_bank_holiday", "🌸",
                   ("Long weekend savings", "Bank holiday treats", "Make the most of the long weekend"),
                   "spring", "Bank Holiday", lead_days=3, duration_days=4, extra={"code": "BANKHOLIDAY"},
                   background_prompt=_BG + "late spring flowers and warm daylight on a light oak table"),
    PromotionTheme("bank_holiday_summer", "Summer Bank Holiday", "occasion", "summer_bank_holiday", "🌻",
                   ("Long weekend savings", "Bank holiday treats", "End-of-summer treats"),
                   "summer", "Bank Holiday", lead_days=3, duration_days=4, extra={"code": "BANKHOLIDAY"},
                   background_prompt=_BG + "golden late-summer light on a garden terrace"),
    # ------------------------------------------------------------------- US only
    PromotionTheme("presidents_day", "Presidents' Day", "occasion", "presidents_day", "🇺🇸",
                   ("Presidents' Day savings", "Long weekend deals", "Treat yourself this weekend"),
                   "fathers_day", "Presidents' Day", lead_days=3, duration_days=4,
                   background_prompt=_BG + "crisp winter morning light, navy and cream textiles"),
    PromotionTheme("mothers_day_us", "Mother's Day (US)", "occasion", "mothers_day_us", "💐",
                   ("For the moms who do it all", "Mother's Day gifts", "Treat Mom to some me-time"),
                   "mothers_day", "Mother's Day", lead_days=14, duration_days=14, extra={"code": "MOMSDAY"},
                   background_prompt=_BG + "fresh peonies and soft morning light on a cream table"),
    PromotionTheme("memorial_day", "Memorial Day", "occasion", "memorial_day", "🇺🇸",
                   ("Memorial Day weekend savings", "Long weekend deals", "Kick off summer for less"),
                   "summer", "Memorial Day", lead_days=4, duration_days=5,
                   background_prompt=_BG + "early summer porch with soft sunlight and greenery"),
    PromotionTheme("independence_day", "4th of July", "occasion", "independence_day", "🎆",
                   ("Happy 4th of July", "Summer savings", "Celebrate with a treat for you"),
                   "summer", "4th of July", lead_days=4, duration_days=5, extra={"code": "JULY4"},
                   background_prompt=_BG + "summer evening with soft sparkler bokeh, navy and warm light"),
    PromotionTheme("labor_day", "Labor Day", "occasion", "labor_day", "🇺🇸",
                   ("Labor Day weekend savings", "Long weekend deals", "Reset for the new season"),
                   "summer", "Labor Day", lead_days=4, duration_days=5,
                   background_prompt=_BG + "late-summer garden table with warm golden light"),
    PromotionTheme("thanksgiving", "Thanksgiving", "occasion", "thanksgiving", "🦃",
                   ("Grateful for you", "Thanksgiving savings", "Give thanks, treat yourself"),
                   "halloween", "Thanksgiving", lead_days=5, duration_days=6,
                   background_prompt=_BG + "autumn table with leaves, small gourds and warm candle light"),
    PromotionTheme("small_business_saturday", "Small Business Saturday", "occasion", "small_business_saturday",
                   "🛍️", ("Thank you for shopping small", "Support a small brand", "Small Business Saturday"),
                   "brand", "Shop Small", lead_days=1, duration_days=2, extra={"code": "SMALLBIZ"},
                   background_prompt=_BG + "cosy independent shop counter with soft warm light"),
    # ---------------------------------------------------------- offers & specials
    PromotionTheme("flash_sale", "Flash Sale", "offer", emoji="⚡",
                   promo_texts=("Flash sale: today only", "48-hour flash sale", "Quick, it won't last"),
                   palette="brand", sticker="Flash Sale", lead_days=0, duration_days=2,
                   content_types=("sale_promo", "countdown", "product_video"),
                   background_prompt=_BG + "dynamic warm light streaks on a minimal studio backdrop"),
    PromotionTheme("clearance", "Clearance", "offer", emoji="🏷️",
                   promo_texts=("Clearance: final reductions", "Last chance to stock up", "While stocks last"),
                   sticker="Clearance", lead_days=0, duration_days=7,
                   background_prompt=_BG + "clean studio shelf with soft shadows"),
    PromotionTheme("special_offer", "Special Offer", "offer", emoji="🎉",
                   promo_texts=("A special offer for you", "Limited-time offer", "Save on our favourites"),
                   sticker="Special Offer", lead_days=0, duration_days=7,
                   background_prompt=_BG + "premium neutral studio backdrop with botanical shadows"),
    PromotionTheme("free_delivery", "Free Delivery", "offer", emoji="🚚",
                   promo_texts=("Free UK delivery", "Delivered free to your door", "No delivery fees this week"),
                   sticker="Free Delivery", lead_days=0, duration_days=5, content_types=("sale_promo", "collage"),
                   background_prompt=_BG + "kraft paper parcel on a sunny doorstep, no labels"),
    PromotionTheme("bundle_save", "Bundle & Save", "offer", emoji="🧺",
                   promo_texts=("Bundle and save", "Better together", "Build your routine for less"),
                   sticker="Bundle & Save", lead_days=0, duration_days=10, content_types=("collage", "sale_promo"),
                   background_prompt=_BG + "wide linen surface with gentle natural light"),
    # ------------------------------------------------------------ new arrivals
    PromotionTheme("new_arrivals", "New Arrivals", "new_arrivals", emoji="🌱",
                   promo_texts=("Just landed", "New in", "Meet our newest arrival"),
                   sticker="New In", lead_days=0, duration_days=7,
                   content_types=("new_arrival", "product_video", "carousel"),
                   background_prompt=_BG + "fresh botanical leaves and morning light on stone"),
    PromotionTheme("back_in_stock", "Back in Stock", "new_arrivals", emoji="🔁",
                   promo_texts=("Back in stock", "It's back", "Restocked and ready"),
                   sticker="Back in Stock", lead_days=0, duration_days=5,
                   content_types=("back_in_stock", "single_product"),
                   background_prompt=_BG + "clean shelf with soft daylight"),
    # ---------------------------------------------------------------- custom
    PromotionTheme("custom", "Custom event", "custom", emoji="📅",
                   promo_texts=(), sticker="", lead_days=0, duration_days=7,
                   background_prompt=_BG + "premium neutral studio backdrop"),
)}

CATEGORIES = {
    "occasion": "Occasions",
    "offer": "Offers & Specials",
    "new_arrivals": "New Arrivals",
    "custom": "Custom",
}


def get_theme(key: str) -> PromotionTheme:
    return THEMES.get(key) or THEMES["custom"]


def theme_palette(key: str) -> dict[str, str]:
    return dict(PALETTES.get(get_theme(key).palette, {}))


def upcoming_calendar(start: date, *, days: int = 365) -> list[dict[str, Any]]:
    """Occasion-based themes with their next date and the suggested promotion window."""
    end = start + timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for theme in THEMES.values():
        if not theme.occasion_key or theme.occasion_key not in OCCASIONS:
            continue
        for year in (start.year, start.year + 1):
            when = occasion_date(theme.occasion_key, year)
            if when is None or not (start <= when <= end):
                continue
            suggested_start = max(start, when - timedelta(days=theme.lead_days))
            rows.append({
                "theme_key": theme.key, "label": theme.label, "emoji": theme.emoji,
                "date": when.isoformat(), "days_until": (when - start).days,
                "suggested_start": suggested_start.isoformat(),
                "suggested_duration": min(15, max(1, (when - suggested_start).days + 1 if theme.lead_days else theme.duration_days)),
                "sticker": theme.sticker, "market": theme.market,
            })
            break
    return sorted(rows, key=lambda r: r["date"])
