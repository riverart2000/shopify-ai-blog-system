"""Grok (xAI) image backend.

Two modes:
  * text-to-image  -> POST /v1/images/generations (no reference images)
  * image editing  -> POST /v1/images/edits       (1-3 reference images)

Image editing is preferred whenever the product's real photos are available, so
the generated marketing image keeps the actual product's shape, colour and
branding instead of reinventing it.
"""

from __future__ import annotations

import base64
from typing import List, Optional

from services.landing_pages.product_prompts.utils import get_logger

from .base import GeneratedImage, ImageBackend

log = get_logger("social.image.grok")

_MAX_PROMPT_CHARS = 4000
_MAX_REFERENCES = 3  # xAI Imagine supports up to 3 source images per edit.

# Prepended to the prompt in edit mode so the model preserves the real product.
_FIDELITY_CLAUSE = (
    "Use the provided reference image(s) as the EXACT product. Reproduce it with the "
    "SAME colour, finish and material as the reference: preserve its exact hue, tone "
    "and reflectivity, and if the product is silver, metallic, grey or light-coloured, "
    "keep it that colour - do NOT darken it or turn it black. Keep the shape, "
    "proportions, logo and branding identical. Do not redesign, restyle, relabel or "
    "recolour the product. Composite this exact product realistically into the "
    "following scene. "
)

# Appended (in edit mode) to the model's Avoid list to stop product recolouring.
_COLOUR_NEGATIVE = (
    "changing the product colour, recolouring the product, black or dark product body, "
    "discoloured product, wrong material or finish, tinted product"
)


def _sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _data_uri(data: bytes) -> str:
    mime = _sniff_mime(data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class GrokImageBackend(ImageBackend):
    name = "grok"

    def __init__(self, settings, session, quality: bool = False) -> None:
        super().__init__(settings, session)
        self.model = (
            settings.grok_image_quality_model
            if quality
            else settings.grok_image_model
        )

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        n: int = 1,
        reference_images: Optional[List[bytes]] = None,
    ) -> List[GeneratedImage]:
        if not self.settings.grok_api_key:
            raise RuntimeError("GROK_API_KEY is required to generate images.")

        refs = [r for r in (reference_images or []) if r][:_MAX_REFERENCES]
        if refs:
            return self._edit(prompt, negative_prompt, n, refs)
        return self._text_to_image(prompt, negative_prompt, n)

    # ------------------------------------------------------------------
    def _text_to_image(self, prompt, negative_prompt, n) -> List[GeneratedImage]:
        payload = {
            "model": self.model,
            "prompt": self._full_prompt(prompt, negative_prompt),
            "n": max(1, n),
            "response_format": "b64_json",
        }
        return self._post("images/generations", payload)

    def _edit(self, prompt, negative_prompt, n, refs) -> List[GeneratedImage]:
        # Single reference -> object form; multiple -> array of data URIs.
        if len(refs) == 1:
            image_field = {"url": _data_uri(refs[0]), "type": "image_url"}
        else:
            image_field = [_data_uri(r) for r in refs]
        # Reinforce colour/finish preservation via the negative prompt too.
        negative_prompt = ", ".join(
            p for p in (negative_prompt.strip(", "), _COLOUR_NEGATIVE) if p
        )
        payload = {
            "model": self.model,
            "prompt": self._full_prompt(prompt, negative_prompt, fidelity=True),
            "image": image_field,
            "n": max(1, n),
            "response_format": "b64_json",
        }
        log.debug("Editing with %d reference image(s)", len(refs))
        return self._post("images/edits", payload)

    # ------------------------------------------------------------------
    def _full_prompt(self, prompt: str, negative_prompt: str, fidelity: bool = False) -> str:
        text = prompt.strip()
        if fidelity:
            text = _FIDELITY_CLAUSE + text
        if negative_prompt:
            text += f"\n\nAvoid: {negative_prompt.strip()}"
        return text[:_MAX_PROMPT_CHARS]

    def _post(self, endpoint: str, payload: dict) -> List[GeneratedImage]:
        resp = self.session.post(
            f"{self.settings.grok_base_url.rstrip('/')}/{endpoint}",
            headers={
                "Authorization": f"Bearer {self.settings.grok_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.settings.grok_timeout,
        )
        resp.raise_for_status()
        body = resp.json()

        images: List[GeneratedImage] = []
        for item in body.get("data", []):
            b64 = item.get("b64_json")
            if not b64:
                continue
            try:
                data = base64.b64decode(b64)
            except (ValueError, TypeError) as exc:
                log.warning("Failed to decode a generated image: %s", exc)
                continue
            images.append(
                GeneratedImage(data=data, mime_type=item.get("mime_type", "image/jpeg"))
            )
        if not images:
            raise RuntimeError(f"Image API ({endpoint}) returned no usable images.")
        log.debug("Generated %d image(s) with model %s", len(images), self.model)
        return images
