"""Load the per-product JSON files produced by the prompt generator.

Works with plain dicts (schema_version 2) so the consumer stays decoupled from
the producer's dataclasses and tolerant of missing keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List, Tuple

from product_prompts.utils import get_logger

log = get_logger("social.loader")


def find_product_files(input_dir: Path) -> List[Path]:
    """Return all product JSON files in ``input_dir`` (non-recursive, sorted)."""
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    files = sorted(
        p
        for p in input_dir.glob("*.json")
        if p.is_file() and not p.name.startswith(".")
    )
    if not files:
        raise FileNotFoundError(f"No product JSON files found in {input_dir}")
    return files


def load_product_file(path: Path) -> dict:
    """Parse and lightly validate one product JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Failed to read product file {path}: {exc}") from exc
    if "creative_concepts" not in data:
        raise ValueError(f"{path} has no 'creative_concepts' — not a product file.")
    return data


def iter_products(input_dir: Path) -> Iterator[Tuple[Path, dict]]:
    for path in find_product_files(input_dir):
        try:
            yield path, load_product_file(path)
        except ValueError as exc:
            log.warning("Skipping %s: %s", path.name, exc)
