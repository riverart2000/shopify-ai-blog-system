"""Loading creative concepts from ``creative_concepts.list``."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .models import CreativeConcept
from .utils import slugify


def parse_concept_line(line: str) -> CreativeConcept:
    """Parse a single line into a :class:`CreativeConcept`.

    Supported formats (all optional whitespace tolerant)::

        Lifestyle Image – Product being used naturally in a real-life setting.
        Lifestyle Image - Product being used naturally...
        Lifestyle Image: Product being used naturally...
        Myth vs Fact

    The name is everything before the first en-dash/hyphen/colon separator;
    the remainder (if any) is the description.
    """
    raw = line.strip()
    name, description = raw, ""
    for sep in ("–", "—", " - ", ": ", ":"):
        if sep in raw:
            head, _, tail = raw.partition(sep)
            name, description = head.strip(), tail.strip()
            break
    return CreativeConcept(name=name, description=description, slug=slugify(name))


def load_concepts(path: Path) -> List[CreativeConcept]:
    """Read and parse all non-empty, non-comment lines from ``path``."""
    if not path.exists():
        raise FileNotFoundError(f"Creative concepts file not found: {path}")

    concepts: List[CreativeConcept] = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        concept = parse_concept_line(stripped)
        if concept.slug in seen:
            continue
        seen.add(concept.slug)
        concepts.append(concept)
    if not concepts:
        raise ValueError(f"No creative concepts parsed from {path}")
    return concepts
