#!/usr/bin/env python3
"""Cyber Briefing Tool — Daily cybersecurity intelligence briefing.

Usage:
    python briefing.py                  # Full run: gather, score, deliver to Bear
    python briefing.py --dry-run        # Full run but print to stdout instead
    python briefing.py --gather-only    # Just gather and show item count
    python briefing.py --stats          # Show database statistics
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env from the project directory
load_dotenv(Path(__file__).parent / ".env")

from collectors import rss, cisa_kev, nvd, hackerone, github_advisories
from collectors import enisa_scraper, ico_scraper, tldr_scraper
from db.state import (
    get_connection,
    filter_unseen,
    mark_seen_batch,
    should_check_scraper,
    update_scraper_run,
    get_stats,
)
from prioritiser.scorer import score_items
from prioritiser.clusterer import cluster_items
from delivery.formatter import format_briefing
from delivery.bear import deliver_to_bear, deliver_to_stdout


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def gather_all(config: dict, db_conn) -> list[dict]:
    """Run all collectors and return unseen items."""
    all_items = []

    # --- Tier 1: Structured APIs ---

    # CISA KEV
    if config.get("sources", {}).get("cisa_kev", {}).get("enabled", True):
        items = cisa_kev.collect()
        all_items.extend(items)

    # NVD
    if config.get("sources", {}).get("nvd", {}).get("enabled", True):
        nvd_config = config.get("nvd", {})
        items = nvd.collect(nvd_config)
        all_items.extend(items)

    # HackerOne
    if config.get("sources", {}).get("hackerone", {}).get("enabled", True):
        items = hackerone.collect()
        all_items.extend(items)

    # GitHub Advisories
    if config.get("sources", {}).get("github_advisories", {}).get("enabled", True):
        items = github_advisories.collect()
        all_items.extend(items)

    # --- Tier 2: RSS Feeds ---

    rss_feeds = config.get("sources", {}).get("rss_feeds", {})
    for feed_name, feed_config in rss_feeds.items():
        try:
            items = rss.collect(feed_config)
            all_items.extend(items)
        except Exception as e:
            logging.getLogger("cyberbriefing").warning(
                "RSS feed %s failed: %s", feed_name, e
            )

    # --- Tier 3: Scrapers (interval-gated) ---

    scrapers = config.get("sources", {}).get("scrapers", {})

    # ENISA
    enisa_conf = scrapers.get("enisa", {})
    if enisa_conf.get("enabled", True):
        interval = enisa_conf.get("check_interval_hours", 24)
        if should_check_scraper(db_conn, "enisa", interval):
            items = enisa_scraper.collect(enisa_conf)
            all_items.extend(items)
            update_scraper_run(db_conn, "enisa")

    # ICO
    ico_conf = scrapers.get("ico", {})
    if ico_conf.get("enabled", True):
        interval = ico_conf.get("check_interval_hours", 168)
        if should_check_scraper(db_conn, "ico", interval):
            items = ico_scraper.collect(ico_conf)
            all_items.extend(items)
            update_scraper_run(db_conn, "ico")

    # TLDR Infosec
    tldr_conf = scrapers.get("tldr_infosec", {})
    if tldr_conf.get("enabled", True):
        interval = tldr_conf.get("check_interval_hours", 23)
        if should_check_scraper(db_conn, "tldr_infosec", interval):
            items = tldr_scraper.collect(tldr_conf)
            all_items.extend(items)
            update_scraper_run(db_conn, "tldr_infosec")

    # --- Filter to unseen items only ---

    new_items = filter_unseen(db_conn, all_items)

    logger = logging.getLogger("cyberbriefing")
    logger.info(
        "Gathered %d total items, %d new (unseen)",
        len(all_items),
        len(new_items),
    )

    return new_items


def run_pipeline(
    config: dict,
    dry_run: bool = False,
    gather_only: bool = False,
) -> bool:
    """Execute the full gather -> prioritise -> deliver pipeline.

    Returns True if the pipeline completed successfully.
    """
    logger = logging.getLogger("cyberbriefing")
    db_conn = get_connection()

    # --- Stage 1: Gather ---
    logger.info("=" * 50)
    logger.info("Stage 1: Gathering from all sources")
    logger.info("=" * 50)

    new_items = gather_all(config, db_conn)

    if gather_only:
        logger.info("Gather-only mode: %d new items found", len(new_items))
        for item in new_items[:20]:
            print(f"  [{item['source']}] {item['title']}")
        if len(new_items) > 20:
            print(f"  ... and {len(new_items) - 20} more")
        # Mark all as seen
        mark_seen_batch(db_conn, new_items, included=False)
        return True

    if not new_items:
        logger.info("No new items to brief on today.")
        if dry_run:
            print("\nNo new items found across any sources.")
        return True

    # --- Stage 2: Prioritise ---
    scoring_config = config.get("scoring", {})
    max_score_input = scoring_config.get("max_score_input", 150)

    # Sort by recency (newest first) and cap before sending to Claude.
    # Handles first-run floods (e.g. full CISA KEV catalogue) gracefully.
    items_to_score = sorted(
        new_items,
        key=lambda x: x.get("published") or "",
        reverse=True,
    )[:max_score_input]

    logger.info("=" * 50)
    logger.info(
        "Stage 2: Scoring %d items with Claude (capped from %d new)",
        len(items_to_score),
        len(new_items),
    )
    logger.info("=" * 50)

    scored_result = score_items(items_to_score, scoring_config)

    scored_items = scored_result.get("items", [])
    if not scored_items:
        logger.warning("Scoring returned no items above threshold")
        mark_seen_batch(db_conn, new_items, included=False)
        return True

    # Cluster related stories
    clustered = cluster_items(scored_items, new_items)

    # --- Stage 3: Deliver ---
    logger.info("=" * 50)
    logger.info("Stage 3: Formatting and delivering %d items", len(clustered))
    logger.info("=" * 50)

    briefing_date = scored_result.get(
        "briefing_date",
        datetime.now(timezone.utc).strftime("%d %B %Y"),
    )
    title, body, tags = format_briefing(clustered, new_items, briefing_date)

    if dry_run:
        success = deliver_to_stdout(title, body, tags)
    else:
        delivery_method = config.get("delivery", {}).get("method", "bear")
        if delivery_method == "bear":
            success = deliver_to_bear(title, body, tags)
        elif delivery_method == "stdout":
            success = deliver_to_stdout(title, body, tags)
        else:
            logger.error("Unknown delivery method: %s", delivery_method)
            success = False

    # Mark items as seen — track which ones were included in the briefing
    included_ids = {item.get("id") for item in scored_items}
    included, excluded = [], []
    for item in new_items:
        (included if item["id"] in included_ids else excluded).append(item)
    mark_seen_batch(db_conn, included, included=True)
    mark_seen_batch(db_conn, excluded, included=False)

    if success:
        logger.info("Briefing delivered successfully: %s", title)
    else:
        logger.error("Briefing delivery failed")

    return success


def show_stats() -> None:
    """Print database statistics."""
    db_conn = get_connection()
    stats = get_stats(db_conn)
    print(f"\nCyber Briefing Database Statistics")
    print(f"{'=' * 40}")
    print(f"Total items seen:     {stats['total_items_seen']}")
    print(f"Items in briefings:   {stats['total_included']}")
    print(f"\nBy source:")
    for source, count in stats["by_source"].items():
        print(f"  {source:25s} {count:5d}")


def main():
    parser = argparse.ArgumentParser(
        description="Cyber Briefing Tool — Daily cybersecurity intelligence briefing"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but print to stdout instead of Bear",
    )
    parser.add_argument(
        "--gather-only",
        action="store_true",
        help="Only gather items and show counts (no scoring or delivery)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.stats:
        show_stats()
        return

    config = load_config()
    success = run_pipeline(
        config,
        dry_run=args.dry_run,
        gather_only=args.gather_only,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
