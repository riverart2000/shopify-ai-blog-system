"""Pluggable prompt generators."""

from .base import PromptGenerator
from .template import TemplatePromptGenerator
from .grok import GrokPromptGenerator

__all__ = [
    "PromptGenerator",
    "TemplatePromptGenerator",
    "GrokPromptGenerator",
    "get_generator",
]


def get_generator(name: str, settings, session):
    """Factory: return a prompt generator by name.

    ``template`` (default) is deterministic and needs no API. ``grok`` uses the
    xAI chat API and falls back to the template generator on any failure.
    """
    name = (name or "template").lower()
    if name in ("template", "offline", "local"):
        return TemplatePromptGenerator(settings)
    if name in ("grok", "xai", "llm"):
        return GrokPromptGenerator(settings, session)
    raise ValueError(f"Unknown prompt generator '{name}'. Use 'template' or 'grok'.")
