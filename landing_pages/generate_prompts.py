#!/usr/bin/env python3
"""CLI entrypoint for the product marketing prompt generator.

Examples
--------
    # Default: web fetcher + deterministic template prompts
    python generate_prompts.py

    # Use the Shopify Admin API and Grok-authored prompts
    python generate_prompts.py --fetcher shopify --generator grok

    # Process a single URL
    python generate_prompts.py --url https://bioluxelab.com/products/xyz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from product_prompts.config import Settings
from product_prompts.pipeline import Pipeline, read_url_list
from product_prompts.utils import configure_logging, get_logger

log = get_logger("cli")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate per-product marketing prompt JSON + images."
    )
    parser.add_argument(
        "--fetcher",
        default="web",
        choices=["web", "shopify"],
        help="Data source for product details (default: web).",
    )
    parser.add_argument(
        "--generator",
        default="template",
        choices=["template", "grok"],
        help="Prompt generation backend (default: template).",
    )
    parser.add_argument(
        "--product-list",
        type=Path,
        default=None,
        help="Path to the product URL list (default: product.list).",
    )
    parser.add_argument(
        "--concepts",
        type=Path,
        default=None,
        help="Path to the creative concepts list (default: creative_concepts.list).",
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        default=None,
        help="Path to the campaign/offer file (default: campaign.txt).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated output (default: output/).",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=None,
        help="Process a single URL (repeatable). Overrides --product-list.",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=3,
        help="Maximum images to download per product (default: 3).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.verbose)

    settings = Settings.load()
    if args.product_list:
        settings.product_list = args.product_list
    if args.concepts:
        settings.concepts_list = args.concepts
    if args.campaign:
        settings.campaign_file = args.campaign
    if args.output_dir:
        settings.output_dir = args.output_dir

    urls = args.url if args.url else read_url_list(settings.product_list)

    pipeline = Pipeline(
        settings,
        fetcher_name=args.fetcher,
        generator_name=args.generator,
        max_images=args.max_images,
    )
    outputs = pipeline.run(urls)

    if not outputs:
        log.error("No products were processed successfully.")
        return 1
    log.info("Done. Generated %d product file(s) in %s", len(outputs), settings.output_dir)
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
