"""Deterministic, API-free prompt generator.

Produces high-quality, concept-specific image prompts, on-image text and social
captions using templates plus lightweight extraction from the product/blog
content. It also derives a best-effort ideal-client persona. This is the default
backend and the fallback for the Grok backend, so it always works offline.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..models import (
    BlogContent,
    Campaign,
    ClientPersona,
    ConceptOutput,
    CreativeConcept,
    ImageText,
    Product,
)
from ..utils import first_sentences, slugify
from .base import PromptGenerator
from .specs import aspect_for, dimensions_for, needs_person

# Shared photographic direction appended to most prompts for quality.
_QUALITY = (
    "ultra-high resolution, professional commercial photography, sharp focus, "
    "natural soft lighting, clean composition, colour-graded, photorealistic, "
    "advertising quality, rendered at 1024px on the longest side"
)

_BASE_NEGATIVE = (
    "low quality, blurry, distorted, watermark, misspelled text, gibberish text, "
    "extra limbs, deformed hands, extra fingers, oversaturated, cluttered "
    "background, warped product, duplicated logo"
)

_MALE_AUDIENCE = re.compile(
    r"\b(?:men(?:'s|s)?|man|male|gentleman|gentlemen|boys?)\b",
    re.IGNORECASE,
)
_FEMALE_AUDIENCE = re.compile(
    r"\b(?:women(?:'s|s)?|woman|female|ladies|lady|girls?)\b",
    re.IGNORECASE,
)


def _exclusive_sex_signal(text: str) -> str:
    """Return a binding audience sex only when one side is unambiguous."""
    male = bool(_MALE_AUDIENCE.search(text or ""))
    female = bool(_FEMALE_AUDIENCE.search(text or ""))
    if male == female:
        return ""
    return "man" if male else "woman"


def infer_target_sex(product: Product, blog: BlogContent | None = None) -> str:
    """Infer audience sex by evidence priority, never by first keyword seen.

    Explicit wording in the product title/handle is binding. Lower-confidence
    product metadata and copy are considered only when higher-priority evidence
    is silent. Blog copy is deliberately last because it can discuss a broader
    audience than the product itself.
    """
    evidence_tiers = [
        f"{product.title} {product.handle}",
        " ".join([product.product_type or "", *product.tags]),
        product.description_text or "",
        blog.text if blog else "",
    ]
    for text in evidence_tiers:
        signal = _exclusive_sex_signal(text)
        if signal:
            return signal
    return ""


def audience_constraint(product: Product, blog: BlogContent | None = None) -> str:
    sex = infer_target_sex(product, blog)
    if sex:
        source = "men" if sex == "man" else "women"
        return (
            f"BINDING: explicit product evidence targets {source}. The persona sex "
            f"MUST be '{sex}', and every person depicted must match. Product-title "
            "audience wording overrides generic description or blog wording."
        )
    return (
        "No explicit sex-specific audience was found. Choose a credible customer "
        "from product evidence without relying on category stereotypes."
    )


def infer_fallback_persona_sex(
    product: Product, blog: BlogContent | None = None
) -> str:
    """Choose a fallback depiction without a universal female default.

    Explicit audience evidence remains authoritative. A small set of strong
    strength-sport signals provides a useful emergency fallback; everything
    else stays non-specific for Grok to resolve from the full evidence.
    """
    explicit = infer_target_sex(product, blog)
    if explicit:
        return explicit
    text = " ".join(
        [
            product.title,
            product.handle,
            product.product_type or "",
            " ".join(product.tags),
            product.description_text or "",
            blog.text if blog else "",
        ]
    ).lower()
    strength_signals = (
        "hand grip",
        "grip strength",
        "forearm",
        "powerlifting",
        "weightlifting",
        "bodybuilding",
        "muscle trainer",
        "strength trainer",
    )
    if any(signal in text for signal in strength_signals):
        return "man"
    return "person"


def _benefit_bullets(text: str, limit: int = 5) -> List[str]:
    """Heuristically pull short benefit-like phrases from description text."""
    if not text:
        return []
    candidates: List[str] = []
    for raw in re.split(r"[\n\.\u2022•]+|(?:\s[-–—]\s)", text):
        phrase = raw.strip(" \t-–—•:")
        if 12 <= len(phrase) <= 90 and not phrase.lower().startswith(
            ("size", "material", "line", "product information", "colour", "color")
        ):
            candidates.append(phrase[0].upper() + phrase[1:])
        if len(candidates) >= limit:
            break
    return candidates


class TemplatePromptGenerator(PromptGenerator):
    name = "template"

    # ------------------------------------------------------------------
    # Persona
    # ------------------------------------------------------------------
    def build_persona(self, product: Product, blog: BlogContent) -> ClientPersona:
        text = f"{product.title} {product.description_text} {' '.join(product.tags)}".lower()
        if blog and blog.text:
            text += " " + blog.text.lower()

        # Product-title evidence is binding; broader copy cannot override it.
        sex = infer_fallback_persona_sex(product, blog)

        # Infer an age band from the concern.
        age = 38
        if "anti-aging" in text or "wrinkle" in text or "mature" in text:
            age = 50
        elif "teen" in text or "acne" in text:
            age = 22

        pain_point = ""
        for keyword, pp in (
            ("hair", "thinning hair and wanting to regrow fuller, healthier hair"),
            ("skin", "dull or ageing skin and wanting a visible glow"),
            ("pain", "everyday aches and wanting drug-free relief"),
            ("sleep", "poor sleep and wanting to feel rested"),
        ):
            if keyword in text:
                pain_point = pp
                break
        if not pain_point:
            pain_point = "looking for an easy, effective self-care upgrade"

        name = {"woman": "Sarah", "man": "David"}.get(sex, "Alex")
        explicit_audience = infer_target_sex(product, blog)
        if explicit_audience:
            rationale = (
                f"The product evidence explicitly targets "
                f"{'men' if explicit_audience == 'man' else 'women'}. The age and "
                "life stage are a broad adult estimate because no more specific "
                "customer evidence was available to the fallback generator."
            )
        else:
            rationale = (
                "This is a broad adult fallback persona based on the product's "
                "problem and benefits; no explicit audience signal was found."
            )
        return ClientPersona(
            name=name,
            age=age,
            sex=sex,
            race="Caucasian",
            ethnicity="mixed / broadly relatable",
            appearance=(
                "healthy, well-groomed, natural everyday look, warm genuine smile"
            ),
            occupation="busy professional",
            location="modern home",
            lifestyle="health-conscious, values convenient self-care",
            pain_point=pain_point,
            description=(
                f"A relatable {age}-year-old {sex} who is {pain_point}. "
                f"Health-conscious and drawn to premium, effective, easy-to-use products."
            ),
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Concept generation
    # ------------------------------------------------------------------
    def generate(
        self,
        product: Product,
        blog: BlogContent,
        concept: CreativeConcept,
        persona: ClientPersona,
        campaign: Campaign,
    ) -> ConceptOutput:
        ctx = self._context(product, blog, concept, persona, campaign)
        builder = _CONCEPT_BUILDERS.get(concept.slug, _generic_builder)
        scene, image_text, social_text = builder(ctx)

        include_person = needs_person(concept.slug)
        aspect = aspect_for(concept.slug)
        width, height = dimensions_for(aspect)

        image_prompt = self._assemble(scene, image_text, include_person, persona, aspect)

        return ConceptOutput(
            concept=concept.name,
            concept_description=concept.description,
            image_prompt=image_prompt,
            social_text=social_text.strip(),
            image_text=image_text,
            include_persona=include_person,
            negative_prompt=_BASE_NEGATIVE,
            aspect_ratio=aspect,
            width=width,
            height=height,
            hashtags=ctx["hashtags"],
            keywords=ctx["keywords"],
        )

    # ------------------------------------------------------------------
    def _assemble(
        self,
        scene: str,
        image_text: ImageText,
        include_person: bool,
        persona: ClientPersona,
        aspect: str,
    ) -> str:
        parts: List[str] = [scene.strip()]
        if include_person:
            parts.append(
                "Feature the ideal customer as the person in the image: "
                f"{persona.visual_description()}. Authentic, natural expression and "
                "posture; not a stock-photo cliché."
            )
        render = image_text.as_render_instructions()
        if render:
            parts.append(render)
        parts.append(f"Aspect ratio {aspect}. {_QUALITY}.")
        return " ".join(p for p in parts if p).strip()

    def _context(
        self,
        product: Product,
        blog: BlogContent,
        concept: CreativeConcept,
        persona: ClientPersona,
        campaign: Campaign,
    ) -> Dict:
        source_text = product.description_text or ""
        if blog and blog.text:
            source_text = f"{source_text}\n{blog.text}"
        benefits = _benefit_bullets(source_text)
        summary = first_sentences(product.description_text or (blog.text if blog else ""))
        hashtags = self._hashtags(product)
        keywords = self._keywords(product, concept)
        return {
            "product": product,
            "blog": blog,
            "concept": concept,
            "persona": persona,
            "campaign": campaign,
            "title": product.title,
            "brand": product.vendor or "the brand",
            "summary": summary,
            "benefits": benefits,
            "benefits_str": "; ".join(benefits) if benefits else summary,
            "hashtags": hashtags,
            "hashtags_str": " ".join(hashtags),
            "keywords": keywords,
            "offer_badge": campaign.badge_text(),
            "offer_code": campaign.code,
            "quality": _QUALITY,
        }

    @staticmethod
    def _hashtags(product: Product) -> List[str]:
        seed: List[str] = []
        for value in [product.product_type, product.vendor, *product.tags]:
            if value:
                seed.append("#" + slugify(value).replace("-", ""))
        seed.extend(["#selfcare", "#wellness", "#beforeandafter", "#viral"])
        seen: List[str] = []
        for tag in seed:
            if tag and tag not in seen and len(tag) > 2:
                seen.append(tag)
        return seen[:8]

    @staticmethod
    def _keywords(product: Product, concept: CreativeConcept) -> List[str]:
        """Best-effort long-tail keyword phrases for discovery/SEO captions."""
        title = product.title.strip()
        brand = (product.vendor or "").strip()
        category = (product.product_type or "").strip()
        phrases: List[str] = []
        if title:
            phrases.append(f"best {title.lower()}")
            phrases.append(f"{title.lower()} review")
            phrases.append(f"where to buy {title.lower()}")
        if brand:
            phrases.append(f"{brand.lower()} {category.lower() or 'products'}".strip())
        if category:
            phrases.append(f"how to use {category.lower()}")
            phrases.append(f"best {category.lower()} for results")
        for tag in product.tags:
            tag = tag.strip().lower()
            if tag:
                phrases.append(tag)
        # Dedupe, drop empties, keep it tight.
        seen: List[str] = []
        for phrase in phrases:
            phrase = " ".join(phrase.split())
            if phrase and phrase not in seen:
                seen.append(phrase)
        return seen[:8]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _offer_text(ctx: Dict) -> ImageText:
    """Base ImageText carrying the campaign offer (shared by builders)."""
    campaign: Campaign = ctx["campaign"]
    it = ImageText()
    if campaign.has_offer:
        it.offer_badge = ctx["offer_badge"]
        if campaign.code:
            it.discount_code = f"CODE: {campaign.code}"
    return it


def _social_offer_suffix(ctx: Dict) -> str:
    campaign: Campaign = ctx["campaign"]
    if not campaign.has_offer:
        return ""
    bits = []
    if ctx["offer_badge"]:
        bits.append(f"🎉 {ctx['offer_badge']}")
    if campaign.code:
        bits.append(f"Use code {campaign.code}")
    return (" " + " — ".join(bits)) if bits else ""


# ----------------------------------------------------------------------
# Concept-specific builders. Each returns (scene, ImageText, social_text).
# ----------------------------------------------------------------------

def _generic_builder(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Marketing image for '{ctx['title']}' by {ctx['brand']}. "
        f"Concept: {ctx['concept'].name} — {ctx['concept'].description}. "
        f"The product is the hero, crisply lit and centred. Key selling points: "
        f"{ctx['benefits_str']}."
    )
    it = _offer_text(ctx)
    it.headline = ctx["title"]
    it.call_to_action = "Shop Now"
    social = f"Meet {ctx['title']}. {ctx['summary']}{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    return scene, it, social


def _lifestyle(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Lifestyle scene in a bright, modern home: the ideal customer naturally using "
        f"'{ctx['title']}' as part of an everyday self-care routine. Candid, warm, "
        f"aspirational mood, soft window light. The product is clearly visible and in use."
    )
    it = _offer_text(ctx)
    it.headline = "Your easiest self-care upgrade"
    it.call_to_action = "Shop the routine"
    social = (
        f"This is what an easy self-care upgrade looks like ✨ {ctx['title']} fits right "
        f"into your day — no fuss, just results.{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _problem_solution(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Split-screen 'before and after' composition. Left panel: the ideal customer "
        f"looking frustrated about their problem. Right panel: the same person confident "
        f"and happy after using '{ctx['title']}'. Clear visual contrast, hopeful tone; "
        f"the product featured on the solution side."
    )
    it = _offer_text(ctx)
    it.headline = "Before & After"
    it.subheadline = "One simple switch"
    it.call_to_action = "Try it today"
    social = (
        f"Tired of the same struggle? {ctx['title']} could be the change you've been "
        f"looking for.{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _educational(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Clean, modern educational infographic explaining how '{ctx['title']}' works. "
        f"Simple 3-step numbered diagram, minimal iconography, generous whitespace, brand "
        f"colours, crisp flat vector style. The product shown subtly as the takeaway."
    )
    it = _offer_text(ctx)
    it.headline = "How it works"
    it.subheadline = "3 simple steps"
    social = (
        f"How it actually works 👇 A quick breakdown of {ctx['title']} and why the science "
        f"makes sense. Save this for later!{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _benefits(ctx: Dict) -> Tuple[str, ImageText, str]:
    bullets = ctx["benefits"][:5] or [ctx["summary"]]
    scene = (
        f"Bold benefits graphic for '{ctx['title']}'. Hero product centred, surrounded by "
        f"3–5 clearly labelled benefit callouts with small icons. Strong visual hierarchy, "
        f"premium brand colours, high contrast, poster-quality advertising design."
    )
    it = _offer_text(ctx)
    it.headline = "Why you'll love it"
    it.subheadline = " • ".join(b for b in bullets[:3])
    it.call_to_action = "Shop Now"
    social = (
        "Why people love it:\n"
        + "\n".join(f"• {b}" for b in bullets)
        + f"{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _social_proof(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Social-proof creative: the ideal customer smiling and holding '{ctx['title']}', "
        f"beside a clean 5-star review card. Authentic testimonial styling, trustworthy and "
        f"warm; the product clearly identifiable."
    )
    it = _offer_text(ctx)
    it.headline = "★★★★★"
    it.subheadline = '"I finally see a difference."'
    it.call_to_action = "Join them"
    social = (
        f"⭐⭐⭐⭐⭐ Our customers keep coming back to {ctx['title']} for a reason — real "
        f"results, real reviews.{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _premium(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"Minimal, elegant premium product photography of '{ctx['title']}'. Studio lighting, "
        f"seamless neutral backdrop, subtle shadows and reflections, refined and luxurious "
        f"mood, generous negative space, editorial composition."
    )
    it = _offer_text(ctx)
    it.headline = ctx["brand"]
    social = (
        f"Crafted for those who expect more. {ctx['title']} — quiet luxury for your everyday "
        f"ritual.{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _myth_vs_fact(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"'Myth vs Fact' two-column graphic about the category of '{ctx['title']}'. Left "
        f"column labelled MYTH with a crossed-out misconception; right column labelled FACT "
        f"with the truth and the product as proof. Clean typographic design, red/green accents."
    )
    it = _offer_text(ctx)
    it.headline = "MYTH vs FACT"
    it.call_to_action = "Learn the truth"
    social = (
        f"Myth vs Fact 🧠 Let's clear this one up — and yes, {ctx['title']} is the proof."
        f"{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _did_you_know(ctx: Dict) -> Tuple[str, ImageText, str]:
    fact = ctx["benefits"][0] if ctx["benefits"] else ctx["summary"]
    scene = (
        f"'Did You Know?' scroll-stopping social graphic. Eye-catching header, one surprising "
        f"fact presented boldly, with '{ctx['title']}' featured as the smart solution. "
        f"Curiosity-driven, clean layout, brand colours."
    )
    it = _offer_text(ctx)
    it.headline = "Did you know?"
    it.subheadline = first_sentences(fact, 90)
    social = (
        f"Did you know? {fact} 🤯 {ctx['title']} makes it effortless."
        f"{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


def _wellness_tip(ctx: Dict) -> Tuple[str, ImageText, str]:
    scene = (
        f"'Quick Wellness Tip' calming social graphic featuring the ideal customer enjoying a "
        f"peaceful self-care moment with '{ctx['title']}'. Soft, soothing palette, spa-like "
        f"aesthetic, uplifting healthy-lifestyle mood."
    )
    it = _offer_text(ctx)
    it.headline = "Quick wellness tip"
    it.call_to_action = "Start today"
    social = (
        f"Quick wellness tip 🌿 Small habits, big difference — and {ctx['title']} makes it easy "
        f"to stay consistent.{_social_offer_suffix(ctx)}\n\n{ctx['hashtags_str']}"
    )
    return scene, it, social


_CONCEPT_BUILDERS = {
    "lifestyle-image": _lifestyle,
    "problem-solution": _problem_solution,
    "educational-infographic": _educational,
    "benefits-graphic": _benefits,
    "social-proof": _social_proof,
    "premium-brand-image": _premium,
    "myth-vs-fact": _myth_vs_fact,
    "did-you-know": _did_you_know,
    "quick-wellness-tip": _wellness_tip,
}
