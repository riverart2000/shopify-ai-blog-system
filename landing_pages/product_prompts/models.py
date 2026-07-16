"""Typed data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class Product:
    """Normalised product record, independent of the fetcher used."""

    url: str
    handle: str
    title: str
    description_html: str = ""
    description_text: str = ""
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    price: Optional[str] = None
    currency: Optional[str] = None
    image_urls: List[str] = field(default_factory=list)
    source: str = "web"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BlogContent:
    """Content extracted from the blog article linked in a product."""

    url: Optional[str] = None
    title: Optional[str] = None
    text: str = ""
    image_urls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CreativeConcept:
    """A single creative concept read from ``creative_concepts.list``."""

    name: str
    description: str = ""
    slug: str = ""


@dataclass
class ClientPersona:
    """The ideal customer for a product, used to depict a real person in the
    generated marketing image where the creative calls for one."""

    name: str = ""
    age: Optional[int] = None
    sex: str = ""
    race: str = ""
    ethnicity: str = ""
    appearance: str = ""
    occupation: str = ""
    location: str = ""
    lifestyle: str = ""
    pain_point: str = ""
    description: str = ""

    def visual_description(self) -> str:
        """A compact, image-model-friendly description of the person."""
        parts: List[str] = []
        if self.age:
            parts.append(f"{self.age}-year-old")
        if self.race:
            parts.append(self.race)
        if self.sex:
            parts.append(self.sex)
        if self.appearance:
            parts.append(f"({self.appearance})")
        if self.occupation:
            parts.append(f", {self.occupation}")
        text = " ".join(parts).strip()
        return text or (self.description or "the ideal customer")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Campaign:
    """Promotional offer details loaded from ``campaign.txt``."""

    raw: str = ""
    headline: str = ""
    discount: str = ""
    code: str = ""
    free_shipping: bool = False

    @property
    def has_offer(self) -> bool:
        return bool(self.raw)

    def badge_text(self) -> str:
        """Short text suitable for an on-image offer badge."""
        bits: List[str] = []
        if self.discount:
            bits.append(self.discount)
        if self.free_shipping:
            bits.append("FREE SHIPPING")
        if not bits and self.raw:
            bits.append(self.raw)
        return " + ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImageText:
    """The exact copy the image model must render inside the image."""

    headline: str = ""
    subheadline: str = ""
    call_to_action: str = ""
    offer_badge: str = ""
    discount_code: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def as_render_instructions(self) -> str:
        """Explicit, precise text-rendering directions for the image model."""
        lines: List[str] = []
        if self.headline:
            lines.append(f'HEADLINE (largest, top third): "{self.headline}"')
        if self.subheadline:
            lines.append(f'SUBHEADLINE (below headline, smaller): "{self.subheadline}"')
        if self.offer_badge:
            lines.append(
                f'OFFER BADGE (bold burst badge, top-right corner): "{self.offer_badge}"'
            )
        if self.discount_code:
            lines.append(f'DISCOUNT CODE (near the CTA): "{self.discount_code}"')
        if self.call_to_action:
            lines.append(
                f'CALL TO ACTION (button-style, bottom): "{self.call_to_action}"'
            )
        if not lines:
            return ""
        return (
            "Render the following text INSIDE the image, spelled EXACTLY as written, "
            "with correct spelling, clean modern sans-serif typography, high legibility "
            "and strong contrast against the background:\n- " + "\n- ".join(lines)
        )


@dataclass
class ConceptOutput:
    """Generated marketing assets for one product + one creative concept."""

    concept: str
    concept_description: str
    image_prompt: str
    social_text: str
    image_text: ImageText = field(default_factory=ImageText)
    include_persona: bool = False
    negative_prompt: str = ""
    aspect_ratio: str = "1:1"
    width: int = 1024
    height: int = 1024
    hashtags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["image_text"] = self.image_text.to_dict()
        return data


@dataclass
class ImageAsset:
    """A downloaded image on disk plus its original source URL."""

    source_url: str
    local_path: str
    role: str = "supporting"  # "main" or "supporting"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FunnelStage:
    """A specific stage in the sales funnel landing page."""
    stage: str
    concept: str
    why: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LandingPagePlan:
    """LLM-recommended sales funnel plan for a landing page, selecting key concepts."""

    funnel_stages: List[FunnelStage] = field(default_factory=list)
    advice: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "funnel_stages": [s.to_dict() for s in self.funnel_stages],
            "advice": self.advice
        }


@dataclass
class ProductOutput:
    """The complete per-product record serialised to ``product.json``."""

    product: Product
    blog: BlogContent
    assets: List[ImageAsset]
    concepts: List[ConceptOutput]
    generator: str
    persona: ClientPersona = field(default_factory=ClientPersona)
    campaign: Campaign = field(default_factory=Campaign)
    landing_page_plan: LandingPagePlan = field(default_factory=LandingPagePlan)
    main_image: Optional[str] = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "generator": self.generator,
            "product": self.product.to_dict(),
            "persona": self.persona.to_dict(),
            "campaign": self.campaign.to_dict(),
            "blog": self.blog.to_dict(),
            "landing_page_plan": self.landing_page_plan.to_dict(),
            "main_image": self.main_image,
            "assets": [a.to_dict() for a in self.assets],
            "creative_concepts": [c.to_dict() for c in self.concepts],
        }
