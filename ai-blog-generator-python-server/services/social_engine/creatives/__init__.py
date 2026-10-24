"""Social Engine: creatives.

Branded social images (Pillow) and classic product videos (ffmpeg), rendered
locally from declarative templates in the BioluxeLab "modern botanical" style.

    TemplateRenderer().render(RenderRequest) -> list[RenderedMedia]
    render_preview(store_id, template_key, fmt)  -> RenderedMedia (sample product, offline)
    templates_for(content_type) / get_builtin(key) / seed_builtin_templates(store_id)
    list_available_templates(store_id, content_type="")

Modules: storage (media files), images (download/cache + Pillow utilities),
brand (colours, contrast, fonts, palettes), layout (layer engine), templates
(built-in designs), library (registry + DB seeding), render (images), video
(classic videos), samples (offline sample products).
"""
from __future__ import annotations

from .base import RenderedMedia, Renderer, RenderProduct, RenderRequest
from .brand import build_palette, resolve_brand
from .library import get_builtin, list_available_templates, seed_builtin_templates, templates_for
from .render import TemplateRenderer, load_store_logo, render_preview
from .storage import delete_files, media_root, prune_orphans, resolve_media_path

__all__ = [
    "RenderProduct", "RenderRequest", "RenderedMedia", "Renderer", "TemplateRenderer", "build_palette",
    "delete_files", "get_builtin", "list_available_templates", "load_store_logo", "media_root", "prune_orphans",
    "render_preview", "resolve_brand", "resolve_media_path", "seed_builtin_templates", "templates_for",
]
