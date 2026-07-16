"""Abstract base class for prompt generators."""

from __future__ import annotations

import abc
from typing import List, Tuple

from ..config import Settings
from ..models import (
    BlogContent,
    Campaign,
    ClientPersona,
    ConceptOutput,
    CreativeConcept,
    Product,
)


class PromptGenerator(abc.ABC):
    """Turn (product, blog, concept) into marketing assets.

    Implementations produce, for one creative concept:
      * ``image_prompt``  – a rich, precise prompt for a cloud image model
      * ``social_text``   – the caption posted alongside the generated image
      * ``image_text``    – the exact copy to render inside the image
      * ``include_persona`` + persona – depict the ideal client when relevant
      * ``negative_prompt``, ``hashtags``, ``aspect_ratio``, ``width/height``
    """

    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abc.abstractmethod
    def build_persona(self, product: Product, blog: BlogContent) -> ClientPersona:
        """Derive the ideal client persona for a product (once per product)."""
        raise NotImplementedError

    @abc.abstractmethod
    def generate(
        self,
        product: Product,
        blog: BlogContent,
        concept: CreativeConcept,
        persona: ClientPersona,
        campaign: Campaign,
    ) -> ConceptOutput:
        raise NotImplementedError

    def generate_all(
        self,
        product: Product,
        blog: BlogContent,
        concepts: List[CreativeConcept],
        persona: ClientPersona,
        campaign: Campaign,
    ) -> List[ConceptOutput]:
        return [
            self.generate(product, blog, concept, persona, campaign)
            for concept in concepts
        ]

    def generate_bundle(
        self,
        product: Product,
        blog: BlogContent,
        concepts: List[CreativeConcept],
        campaign: Campaign,
    ) -> Tuple[ClientPersona, List[ConceptOutput], Any]:
        """Produce the persona and every concept for a product, plus a landing page plan.

        The default builds the persona then generates each concept. Backends
        that can do this in fewer API calls (e.g. one batched request) should
        override this method.
        """
        from ..models import FunnelStage, LandingPagePlan
        persona = self.build_persona(product, blog)
        outputs = self.generate_all(product, blog, concepts, persona, campaign)
        
        # Default simple landing page plan for fallbacks
        plan = LandingPagePlan()
        if outputs:
            plan.funnel_stages = [FunnelStage("Hero / Hook", outputs[0].concept, "Main feature")]
            if len(outputs) > 1:
                plan.funnel_stages.append(FunnelStage("Benefits", outputs[1].concept, "Show benefits"))
            if len(outputs) > 2:
                plan.funnel_stages.append(FunnelStage("Social Proof", outputs[2].concept, "Build trust"))
            plan.advice = "Default fallback funnel plan using the first three concepts."
            
        return persona, outputs, plan

