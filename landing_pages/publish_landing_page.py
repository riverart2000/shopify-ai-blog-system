"""Publish a Shopify landing page from the generated marketing assets."""

import argparse
import sys
from pathlib import Path

from product_prompts.config import Settings
from product_prompts.utils import get_logger
from social_publisher.landing_page import LandingPagePublisher

log = get_logger("publish_page.cli")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a high-quality Shopify landing page from generated assets."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output"),
        help="Directory containing the product.json files (default: output)",
    )
    parser.add_argument(
        "--social-dir",
        type=Path,
        default=Path("social"),
        help="Directory containing the generated images (default: social)",
    )
    parser.add_argument(
        "--published",
        action="store_true",
        help="Publish the page immediately (default is draft/hidden)",
    )
    parser.add_argument(
        "--concept-filter",
        nargs="+",
        help="Only include these concepts in the landing page (e.g. 'Lifestyle Image' 'Social Proof')",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    settings = Settings.load()
    from product_prompts.utils import configure_logging
    configure_logging(args.verbose)

    try:
        publisher = LandingPagePublisher(settings, concept_filter=args.concept_filter)
        publisher.run(args.input, args.social_dir, args.published)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        return 1
    except Exception as exc:
        log.exception("Fatal error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
