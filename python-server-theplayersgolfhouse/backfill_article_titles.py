#!/usr/bin/env python3
"""Reusable CLI for cleaning heading-marker noise from existing Shopify article titles."""
from __future__ import annotations

import argparse
import asyncio
import json

import db
from services.article_title_backfill import backfill_article_titles


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strip leaked heading markers (e.g. 'H2:', '## ', quotes) from existing Shopify blog article titles.",
    )
    parser.add_argument("--store-id", default="", help="Store id from the Python server database. Defaults to the first configured store.")
    parser.add_argument("--article-id", action="append", type=int, default=[], help="Specific article id to process. Can be repeated.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of articles to process. 0 means no limit.")
    parser.add_argument("--limit-per-blog", type=int, default=250, help="How many articles to fetch per Shopify blog when scanning.")
    parser.add_argument("--db-path", default="data/ai_blog_server.db", help="SQLite database path.")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without updating any titles.")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress every N processed articles.")
    parser.add_argument("--verbose", action="store_true", help="Print each candidate or result row.")
    parser.add_argument("--json", action="store_true", help="Print the final summary as JSON.")
    return parser


def _print_progress(progress: dict) -> None:
    print(
        "Progress: "
        f"{progress['processed']}/{progress['total']} processed | "
        f"updated {progress['updated']} | failed {progress['failed']}"
    )


async def _run(args: argparse.Namespace) -> int:
    db.set_db_path(args.db_path)
    await db.init_db()

    summary = await backfill_article_titles(
        store_id=args.store_id,
        article_ids=args.article_id,
        limit=args.limit,
        limit_per_blog=args.limit_per_blog,
        dry_run=args.dry_run,
        progress_every=args.progress_every,
        progress_callback=None if args.dry_run else _print_progress,
    )

    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=True, indent=2))
        return 0 if summary.failed == 0 else 1

    action = "would clean" if summary.dry_run else "cleaned"
    print(
        f"Store {summary.store_id} ({summary.myshopify_domain}) scanned {summary.scanned} article(s); "
        f"{summary.candidates} title(s) {action}."
    )

    if args.verbose:
        for result in summary.results:
            line = f"[{result.status}] {result.article_id} | {result.old_title!r} -> {result.new_title!r}"
            if result.error:
                line += f" | {result.error}"
            print(line)

    if summary.dry_run:
        print("Dry run complete. Re-run without --dry-run to apply.")
        return 0

    print(f"Backfill complete. Updated {summary.updated}, failed {summary.failed}.")
    return 0 if summary.failed == 0 else 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
