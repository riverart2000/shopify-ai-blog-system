"""Abstract base class for image-generation backends."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List, Optional

from product_prompts.config import Settings


@dataclass
class GeneratedImage:
    """A single generated image held in memory."""

    data: bytes
    mime_type: str = "image/jpeg"

    @property
    def extension(self) -> str:
        return {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(self.mime_type.lower(), ".jpg")


class ImageBackend(abc.ABC):
    """Turn a text prompt into one or more rendered images.

    Concrete backends encapsulate a specific image model / API. The pipeline
    depends only on this interface, so new providers can be added by dropping in
    a new subclass and registering it in ``get_image_backend``.
    """

    name: str = "base"

    def __init__(self, settings: Settings, session) -> None:
        self.settings = settings
        self.session = session

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        n: int = 1,
        reference_images: "Optional[List[bytes]]" = None,
    ) -> List[GeneratedImage]:
        """Render ``n`` images for ``prompt`` and return them as bytes.

        If ``reference_images`` are supplied, the backend should use them as
        source/reference images (image editing) so real subjects — e.g. the
        actual product — are preserved rather than reinvented.
        """
        raise NotImplementedError
