"""Grok (xAI) prompt generator.

Uses the xAI chat-completions API to profile the ideal client persona and author
rich, precise image prompts, on-image text and captions. By default it does this
for a whole product in a SINGLE batched request (persona + every concept), which
minimises API calls. On any failure it transparently falls back to the
deterministic :class:`TemplatePromptGenerator`, so the pipeline never hard-fails.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from ..models import (
    BlogContent,
    Campaign,
    ClientPersona,
    ConceptOutput,
    CreativeConcept,
    ImageText,
    Product,
)
from ..utils import first_sentences, get_logger
from .base import PromptGenerator
from .specs import aspect_for, dimensions_for, needs_person
from .template import TemplatePromptGenerator, audience_constraint, infer_target_sex

log = get_logger("prompting.grok")

_SYSTEM = (
    "You are a senior direct-response marketing creative, brand strategist and "
    "prompt engineer. You write vivid, precise prompts for a cloud text-to-image "
    "model and punchy social captions that drive sales. You never invent product "
    "claims unsupported by the provided context. You always return STRICT JSON."
)


def _product_evidence(product: Product, blog: BlogContent | None) -> str:
    """Build labelled, balanced evidence instead of choosing one text source.

    Product copy remains primary, while the linked article supplies the customer
    problems, use cases and language that often make a persona more specific.
    """
    tags = ", ".join(tag for tag in product.tags if tag) or "not supplied"
    price = " ".join(part for part in (product.price, product.currency) if part)
    lines = [
        f"Title: {product.title}",
        f"Handle: {product.handle}",
        f"Brand/vendor: {product.vendor or 'not supplied'}",
        f"Product type: {product.product_type or 'not supplied'}",
        f"Tags: {tags}",
        f"Price: {price or 'not supplied'}",
        "Product description: "
        + (first_sentences(product.description_text or "", 2500) or "not supplied"),
    ]
    if blog:
        lines.extend(
            [
                f"Linked guide title: {blog.title or 'not supplied'}",
                "Linked guide customer/problem context: "
                + (first_sentences(blog.text or "", 1600) or "not supplied"),
            ]
        )
    if product.image_urls:
        lines.append(
            "Product photography: attached to this request. Use it as supporting "
            "evidence for product design, presentation and intended use."
        )
    return "\n".join(lines)

_PERSONA_PROMPT = """Profile the single IDEAL customer for the product below, so a
marketing image can depict a believable, specific person.

PRODUCT EVIDENCE:
{evidence}
AUDIENCE CONSTRAINT: {audience_constraint}

Derive every persona choice from the evidence. Product-title audience wording is
binding. Do not use a generic wellness/beauty stereotype. Choose age, occupation,
life stage, pain point and lifestyle because they fit the use case, price and copy.
For a gender-neutral title, choose the most commercially likely primary buyer from
the combined use cases, customer language and attached product photography; never
default automatically to a woman or a man.
If evidence does not support a narrow assumption, say so in the rationale rather
than inventing certainty.

Return STRICT JSON with exactly these keys:
{{
  "name": "first name",
  "age": 42,
  "sex": "woman|man",
  "race": "concrete e.g. Black, East Asian, Hispanic, White/Caucasian, South Asian",
  "ethnicity": "short",
  "appearance": "concrete visual description for an image model (hair, build, style, expression)",
  "occupation": "short",
  "location": "short",
  "lifestyle": "short",
  "pain_point": "the specific problem this product solves for them",
  "description": "1-2 sentence summary of who they are",
  "rationale": "1-2 sentences citing the product evidence behind sex, age, life stage and pain point"
}}"""

_CONCEPT_PROMPT = """Create ONE marketing creative for the product below.

PRODUCT TITLE: {title}
BRAND: {brand}
PRODUCT SUMMARY: {summary}
PRODUCT EVIDENCE:
{evidence}
AUDIENCE CONSTRAINT: {audience_constraint}

IDEAL CUSTOMER (depict this exact person if the concept includes a person):
{persona}

CREATIVE CONCEPT: {concept_name}
CONCEPT GUIDANCE: {concept_desc}
SHOULD THE IMAGE INCLUDE THE PERSON ABOVE: {include_person}
TARGET ASPECT RATIO: {aspect} (render at 1024px on the longest side)

PROMOTION TO FEATURE (if any): {offer}
DISCOUNT CODE: {code}

Requirements:
- The image_prompt must be DETAILED and PRECISE: describe subject, setting,
  composition/framing, lighting, mood, colours and style so the model produces
  exactly this image. If a person is included, describe them using the ideal
  customer above.
- Specify the EXACT text to render inside the image via the image_text fields,
  spelled correctly. Include the promotion as an offer badge when it makes sense
  for the concept. Keep on-image text short and punchy.
- The image_prompt should explicitly instruct the model to render that text
  accurately and legibly, and state where each text element goes.
- Provide 4-8 "keywords": specific long-tail search phrases (3-6 words) a real
  buyer would type, mixing the product, brand and the problem it solves.

Return STRICT JSON with exactly these keys:
{{
  "image_prompt": "detailed prompt (3-6 sentences) including where on-image text is placed",
  "social_text": "scroll-stopping caption + call to action (this is posted alongside the image, NOT rendered in it)",
  "image_text": {{
    "headline": "", "subheadline": "", "call_to_action": "",
    "offer_badge": "", "discount_code": ""
  }},
  "negative_prompt": "comma-separated things to avoid",
  "hashtags": ["#tag1", "#tag2"],
  "keywords": ["long-tail search phrase a buyer would type", "another 3-6 word phrase"]
}}"""


_BUNDLE_PROMPT = """You are creating a full marketing set for ONE product across
several creative concepts, plus the ideal-customer profile.

PRODUCT TITLE: {title}
BRAND: {brand}
PRODUCT SUMMARY: {summary}
PRODUCT EVIDENCE:
{evidence}
AUDIENCE CONSTRAINT: {audience_constraint}

PROMOTION TO FEATURE (if any): {offer}
DISCOUNT CODE: {code}
DEFAULT: render everything at 1024px on the longest side, correct spelling in all
on-image text.

CREATIVE CONCEPTS (produce one creative for EACH, in this order):
{concepts}

First, profile the single IDEAL customer for this product (a believable, specific
person a marketing image can depict). Derive the persona from the supplied evidence,
not from generic category stereotypes. Age, occupation, life stage and pain point
must fit the product's use case, price and customer language. If the evidence does
not justify a narrow assumption, acknowledge that in the rationale. Then produce
one creative per concept.
Finally, design a focused sales funnel landing page for this product. Pick 3-5 of the
best concepts that work together to encourage the customer to buy. Map them to a
sales funnel structure (e.g. Hero/Hook, Agitation/Problem, Solution/Benefits, Social Proof, CTA).

For each concept:
- Treat the AUDIENCE CONSTRAINT as binding. Never contradict an explicit audience
  word in the product title with the persona, imagery or copy.
- If "include_person" is true, depict the ideal customer described in "persona".
  If false, keep it product/graphic focused with no person.
- image_prompt must be DETAILED and PRECISE: subject, setting, composition/framing,
  lighting, mood, colours, style — so the model produces exactly this image.
- Specify EXACT on-image text via image_text (spelled correctly, short, punchy).
  Include the promotion as an offer badge where it suits the concept.
- image_prompt must explicitly instruct the model to render that text accurately
  and legibly, stating where each text element is placed.
- Provide 4-8 "keywords": specific long-tail search phrases (3-6 words) a real
  buyer would type, mixing the product, brand and the problem it solves.
- Respect each concept's target "aspect".
- social_text is the caption posted ALONGSIDE the image (not rendered in it).

Return STRICT JSON with EXACTLY this shape:
{{
  "persona": {{
    "name": "", "age": 42, "sex": "woman|man", "race": "concrete",
    "ethnicity": "", "appearance": "concrete visual description",
    "occupation": "", "location": "", "lifestyle": "",
    "pain_point": "", "description": "1-2 sentence summary",
    "rationale": "evidence behind sex, age, life stage and pain point"
  }},
  "concepts": [
    {{
      "concept": "<exact concept name>",
      "image_prompt": "detailed prompt incl. text placement",
      "social_text": "caption + CTA",
      "image_text": {{
        "headline": "", "subheadline": "", "call_to_action": "",
        "offer_badge": "", "discount_code": ""
      }},
      "negative_prompt": "comma-separated things to avoid",
      "hashtags": ["#tag1", "#tag2"],
      "keywords": ["long-tail search phrase a buyer would type", "another 3-6 word phrase"]
    }}
  ],
  "landing_page_plan": {{
    "funnel_stages": [
      {{"stage": "Hero / Hook", "concept": "<exact concept name 1>", "why": "Why this hooks them"}},
      {{"stage": "Social Proof", "concept": "<exact concept name 2>", "why": "Builds trust"}}
    ],
    "advice": "Tell us how to structure the landing page with these concepts and why they work well together."
  }}
}}
Return one entry in "concepts" for every concept listed above, in the same order."""


class GrokPromptGenerator(PromptGenerator):
    name = "grok"

    def __init__(self, settings, session) -> None:
        super().__init__(settings)
        self.session = session
        self._fallback = TemplatePromptGenerator(settings)

    # ------------------------------------------------------------------
    # Batched path: persona + all concepts in ONE API call.
    # ------------------------------------------------------------------
    def generate_bundle(
        self,
        product: Product,
        blog: BlogContent,
        concepts: List[CreativeConcept],
        campaign: Campaign,
    ) -> Tuple[ClientPersona, List[ConceptOutput], Any]:
        if not self.settings.grok_api_key:
            return super().generate_bundle(product, blog, concepts, campaign)
        try:
            data = self._call_bundle(product, blog, concepts, campaign)
            persona = self._parse_persona(data.get("persona") or {})
            persona, corrected = self._enforce_persona(product, blog, persona)
            if corrected:
                # The batch concepts were authored around the rejected persona,
                # so none of them are safe to reuse for people-focused imagery.
                outputs = self._fallback.generate_all(
                    product, blog, concepts, persona, campaign
                )
            else:
                outputs = self._parse_bundle_concepts(
                    data.get("concepts") or [], concepts, product, persona, blog, campaign
                )
            
            from ..models import FunnelStage, LandingPagePlan
            lp_data = data.get("landing_page_plan") or {}
            stages_data = lp_data.get("funnel_stages", [])
            stages = [
                FunnelStage(
                    stage=s.get("stage", ""),
                    concept=s.get("concept", ""),
                    why=s.get("why", "")
                )
                for s in stages_data if isinstance(s, dict)
            ]
            
            plan = LandingPagePlan(
                funnel_stages=stages,
                advice=lp_data.get("advice", "")
            )
            
            if not plan.funnel_stages and outputs:
                plan.funnel_stages = [FunnelStage("Hero / Hook", outputs[0].concept, "Main feature")]
                if len(outputs) > 1:
                    plan.funnel_stages.append(FunnelStage("Benefits", outputs[1].concept, "Show benefits"))
                if len(outputs) > 2:
                    plan.funnel_stages.append(FunnelStage("Social Proof", outputs[2].concept, "Build trust"))
                plan.advice = "Default fallback funnel plan."

            return persona, outputs, plan
        except Exception as exc:  # noqa: BLE001 - single-call fallback
            log.warning(
                "Grok batched generation failed (%s); falling back to per-concept.",
                exc,
            )
            return super().generate_bundle(product, blog, concepts, campaign)

    def _call_bundle(self, product, blog, concepts, campaign) -> dict:
        concept_lines = "\n".join(
            "- {name}: {desc} | include_person={person} | aspect={aspect}".format(
                name=c.name,
                desc=c.description or c.name,
                person=str(needs_person(c.slug)).lower(),
                aspect=aspect_for(c.slug),
            )
            for c in concepts
        )
        prompt = _BUNDLE_PROMPT.format(
            title=product.title,
            brand=product.vendor or "the brand",
            summary=first_sentences(product.description_text or "", 400),
            evidence=_product_evidence(product, blog),
            offer=campaign.badge_text() or "none",
            code=campaign.code or "none",
            concepts=concept_lines,
            audience_constraint=audience_constraint(product, blog),
        )
        return self._chat(prompt, product.image_urls)

    def _enforce_persona(
        self, product: Product, blog: BlogContent, persona: ClientPersona
    ) -> Tuple[ClientPersona, bool]:
        """Reject an LLM persona that contradicts explicit product evidence."""
        required = infer_target_sex(product, blog)
        supplied = str(persona.sex or "").strip().lower()
        supplied = {"male": "man", "female": "woman"}.get(supplied, supplied)
        if required and supplied != required:
            log.warning(
                "Rejected persona sex=%r for product %r; explicit audience requires %s",
                persona.sex,
                product.title,
                required,
            )
            return self._fallback.build_persona(product, blog), True
        return persona, False

    def _parse_persona(self, data: dict) -> ClientPersona:
        age = data.get("age")
        return ClientPersona(
            name=data.get("name", "") or "",
            age=int(age) if str(age).isdigit() else None,
            sex=data.get("sex", "") or "",
            race=data.get("race", "") or "",
            ethnicity=data.get("ethnicity", "") or "",
            appearance=data.get("appearance", "") or "",
            occupation=data.get("occupation", "") or "",
            location=data.get("location", "") or "",
            lifestyle=data.get("lifestyle", "") or "",
            pain_point=data.get("pain_point", "") or "",
            description=data.get("description", "") or "",
            rationale=data.get("rationale", "") or "",
        )

    def _parse_bundle_concepts(
        self, items, concepts, product, persona, blog, campaign
    ) -> List[ConceptOutput]:
        # Index returned items by normalised concept name for robust matching.
        by_name = {}
        for item in items:
            key = str(item.get("concept", "")).strip().lower()
            if key:
                by_name[key] = item

        outputs: List[ConceptOutput] = []
        for index, concept in enumerate(concepts):
            item = by_name.get(concept.name.strip().lower())
            if item is None and index < len(items):
                item = items[index]  # positional fallback
            if not item or not (item.get("image_prompt") or "").strip():
                # Missing/empty entry: fill deterministically so the set is complete.
                outputs.append(
                    self._fallback.generate(product, blog, concept, persona, campaign)
                )
                continue
            outputs.append(
                self._to_output(
                    item, concept, product, needs_person(concept.slug),
                    aspect_for(concept.slug),
                )
            )
        return outputs

    # ------------------------------------------------------------------
    def build_persona(self, product: Product, blog: BlogContent) -> ClientPersona:
        if not self.settings.grok_api_key:
            return self._fallback.build_persona(product, blog)
        prompt = _PERSONA_PROMPT.format(
            evidence=_product_evidence(product, blog),
            audience_constraint=audience_constraint(product, blog),
        )
        try:
            data = self._chat(prompt, product.image_urls)
            age = data.get("age")
            persona = ClientPersona(
                name=data.get("name", ""),
                age=int(age) if isinstance(age, (int, float, str)) and str(age).isdigit() else None,
                sex=data.get("sex", ""),
                race=data.get("race", ""),
                ethnicity=data.get("ethnicity", ""),
                appearance=data.get("appearance", ""),
                occupation=data.get("occupation", ""),
                location=data.get("location", ""),
                lifestyle=data.get("lifestyle", ""),
                pain_point=data.get("pain_point", ""),
                description=data.get("description", ""),
                rationale=data.get("rationale", ""),
            )
            return self._enforce_persona(product, blog, persona)[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("Grok persona failed (%s); using heuristic persona.", exc)
            return self._fallback.build_persona(product, blog)

    # ------------------------------------------------------------------
    def generate(
        self,
        product: Product,
        blog: BlogContent,
        concept: CreativeConcept,
        persona: ClientPersona,
        campaign: Campaign,
    ) -> ConceptOutput:
        if not self.settings.grok_api_key:
            return self._fallback.generate(product, blog, concept, persona, campaign)
        include_person = needs_person(concept.slug)
        aspect = aspect_for(concept.slug)
        try:
            data = self._call_concept(
                product, blog, concept, persona, campaign, include_person, aspect
            )
            return self._to_output(
                data, concept, product, include_person, aspect
            )
        except Exception as exc:  # noqa: BLE001 - deliberate broad fallback
            log.warning(
                "Grok generation failed for '%s' (%s); falling back to template.",
                concept.name,
                exc,
            )
            return self._fallback.generate(product, blog, concept, persona, campaign)

    # ------------------------------------------------------------------
    def _call_concept(
        self, product, blog, concept, persona, campaign, include_person, aspect
    ):
        prompt = _CONCEPT_PROMPT.format(
            title=product.title,
            brand=product.vendor or "the brand",
            summary=first_sentences(product.description_text or "", 400),
            evidence=_product_evidence(product, blog),
            audience_constraint=audience_constraint(product, blog),
            persona=persona.description or persona.visual_description(),
            concept_name=concept.name,
            concept_desc=concept.description or concept.name,
            include_person="YES" if include_person else "NO (product-focused)",
            aspect=aspect,
            offer=campaign.badge_text() or "none",
            code=campaign.code or "none",
        )
        return self._chat(prompt, product.image_urls)

    def _chat(self, user_prompt: str, image_urls: Optional[List[str]] = None) -> dict:
        images = [url for url in (image_urls or []) if url][:2]
        user_content: Any = user_prompt
        if images:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": url, "detail": "high"},
                }
                for url in images
            ]
            user_content.append({"type": "text", "text": user_prompt})
        payload = {
            "model": self.settings.grok_model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
        }
        resp = self.session.post(
            f"{self.settings.grok_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.grok_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.settings.grok_timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def _to_output(
        self, data: dict, concept: CreativeConcept, product: Product,
        include_person: bool, aspect: str,
    ) -> ConceptOutput:
        image_prompt = (data.get("image_prompt") or "").strip()
        if not image_prompt:
            raise ValueError("Grok returned empty image_prompt")

        it_data = data.get("image_text") or {}
        image_text = ImageText(
            headline=it_data.get("headline", "") or "",
            subheadline=it_data.get("subheadline", "") or "",
            call_to_action=it_data.get("call_to_action", "") or "",
            offer_badge=it_data.get("offer_badge", "") or "",
            discount_code=it_data.get("discount_code", "") or "",
        )

        hashtags = data.get("hashtags") or self._fallback._hashtags(product)
        if isinstance(hashtags, str):
            hashtags = [h.strip() for h in hashtags.split() if h.strip()]

        keywords = data.get("keywords") or self._fallback._keywords(product, concept)
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]

        width, height = dimensions_for(aspect)
        return ConceptOutput(
            concept=concept.name,
            concept_description=concept.description,
            image_prompt=image_prompt,
            social_text=(data.get("social_text") or "").strip(),
            image_text=image_text,
            include_persona=include_person,
            negative_prompt=(data.get("negative_prompt") or "").strip(),
            aspect_ratio=aspect,
            width=width,
            height=height,
            hashtags=hashtags,
            keywords=keywords,
        )
