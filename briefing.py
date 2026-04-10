#!/usr/bin/env python3
"""Cyber Briefing Tool — Daily cybersecurity intelligence briefing.

Usage:
    python briefing.py                        # Full run: gather, score, deliver to Bear
    python briefing.py --dry-run              # Full run but print to stdout instead
    python briefing.py --gather-only          # Just gather and show item count
    python briefing.py --stats                # Show database statistics
    python briefing.py --clear-source <name>  # Reset seen-state for one source
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env from the project directory
load_dotenv(Path(__file__).parent / ".env")

from collectors import rss, cisa_kev, nvd, hackerone, github_advisories
from collectors import enisa_scraper, ico_scraper, tldr_scraper, cloudseclist_scraper, aikido_scraper, twis_scraper, anthropic_red_scraper
from db.state import (
    get_connection,
    filter_unseen,
    mark_seen_batch,
    should_check_scraper,
    update_scraper_run,
    get_stats,
    clear_source,
    prune_old_unseen,
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


# ---------------------------------------------------------------------------
# Scraper registry
# Each entry: (config_key, module, default_interval_hours)
# config_key matches the key under sources.scrapers in config.yaml.
# ---------------------------------------------------------------------------

_SCRAPER_REGISTRY = [
    ("enisa",                  enisa_scraper,        24),
    ("ico",                    ico_scraper,          168),
    ("tldr_infosec",           tldr_scraper,          23),
    ("cloudseclist",           cloudseclist_scraper,  24),
    ("aikido",                 aikido_scraper,        23),
    ("this_week_in_security",  twis_scraper,         168),
    ("anthropic_red",          anthropic_red_scraper, 24),
]


def _run_scraper(db_conn, scrapers_config: dict, name: str, module, default_interval: int) -> list[dict]:
    """Gate a scraper on its enabled flag and interval, then collect."""
    conf = scrapers_config.get(name, {})
    if not conf.get("enabled", True):
        return []
    interval = conf.get("check_interval_hours", default_interval)
    if not should_check_scraper(db_conn, name, interval):
        return []
    items = module.collect(conf)
    update_scraper_run(db_conn, name)
    return items


def gather_all(config: dict, db_conn) -> list[dict]:
    """Run all collectors and return unseen items."""
    all_items = []

    # --- Tier 1: Structured APIs ---

    if config.get("sources", {}).get("cisa_kev", {}).get("enabled", True):
        all_items.extend(cisa_kev.collect())

    if config.get("sources", {}).get("nvd", {}).get("enabled", True):
        all_items.extend(nvd.collect(config.get("nvd", {})))

    if config.get("sources", {}).get("hackerone", {}).get("enabled", True):
        all_items.extend(hackerone.collect())

    if config.get("sources", {}).get("github_advisories", {}).get("enabled", True):
        all_items.extend(github_advisories.collect())

    # --- Tier 2: RSS Feeds (fetched in parallel) ---

    rss_feeds = config.get("sources", {}).get("rss_feeds", {})
    rss_logger = logging.getLogger("cyberbriefing")

    def _fetch_feed(feed_name_and_config):
        feed_name, feed_config = feed_name_and_config
        try:
            return rss.collect(feed_config)
        except Exception as e:
            rss_logger.warning("RSS feed %s failed: %s", feed_name, e)
            return []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_feed, item): item[0]
            for item in rss_feeds.items()
        }
        for future in as_completed(futures):
            all_items.extend(future.result())

    # --- Tier 3: Scrapers (interval-gated) ---

    scrapers = config.get("sources", {}).get("scrapers", {})
    for name, module, default_interval in _SCRAPER_REGISTRY:
        try:
            all_items.extend(_run_scraper(db_conn, scrapers, name, module, default_interval))
        except Exception as e:
            logging.getLogger("cyberbriefing").warning("Scraper %s failed: %s", name, e)

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

    # --- Periodic DB maintenance (auto-prunes items >180 days old that were
    #     never included in a briefing; runs at most once a month) ---
    _maybe_prune(db_conn)

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
        if not dry_run:
            mark_seen_batch(db_conn, new_items, included=False)
        return True

    clustered = cluster_items(scored_items, new_items)

    # --- Stage 3: Deliver ---
    logger.info("=" * 50)
    logger.info("Stage 3: Formatting and delivering %d items", len(clustered))
    logger.info("=" * 50)

    briefing_date = datetime.now().strftime("%Y-%m-%d")
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

    if not dry_run:
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


def _maybe_prune(db_conn) -> None:
    """Auto-prune old unseen items at most once per month."""
    logger = logging.getLogger("cyberbriefing")
    # Re-use the scraper_runs table to track when we last pruned
    if not should_check_scraper(db_conn, "_db_prune", interval_hours=24 * 30):
        return
    removed = prune_old_unseen(db_conn, days=180)
    if removed:
        logger.info("DB maintenance: pruned %d old unseen items", removed)
    update_scraper_run(db_conn, "_db_prune")


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


# ---------------------------------------------------------------------------
# Coloured help formatter
# ---------------------------------------------------------------------------

class _ColouredHelp(argparse.HelpFormatter):
    """argparse formatter with ANSI colour and consistent two-column alignment.

    argparse calculates column widths from raw string lengths, which breaks when
    ANSI escape codes are present (they are invisible but counted as characters).
    We sidestep this by: (a) setting a fixed help_position so alignment is never
    computed from coloured strings, and (b) building each action line manually
    so the flag and help text are always in the right columns.
    """

    BOLD   = "\033[1m"
    CYAN   = "\033[36m"
    YELLOW = "\033[33m"
    GREEN  = "\033[32m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    # Fixed indent for the help column — wide enough for --clear-source SOURCE
    HELP_COL = 28

    def __init__(self, prog):
        super().__init__(prog, max_help_position=self.HELP_COL, width=100)

    def start_section(self, heading):
        heading = f"{self.YELLOW}{self.BOLD}{heading}{self.RESET}" if heading else heading
        super().start_section(heading)

    def _format_action_invocation(self, action):
        """Return the plain (uncoloured) flag text — used for width calculation."""
        return super()._format_action_invocation(action)

    def _format_action(self, action):
        """Render one action line with consistent column alignment."""
        # Plain flag text (no colour) so len() is accurate for padding
        flag_plain = self._format_action_invocation(action)
        flag_coloured = f"{self.CYAN}{self.BOLD}{flag_plain}{self.RESET}"

        help_text = self._expand_help(action) if action.help else ""
        help_coloured = f"{self.DIM}{help_text}{self.RESET}" if help_text else ""

        indent = "  "  # two-space left margin
        gap = max(1, self.HELP_COL - len(flag_plain) - len(indent))

        if help_coloured:
            line = f"{indent}{flag_coloured}{' ' * gap}{help_coloured}"
        else:
            line = f"{indent}{flag_coloured}"

        return line + "\n"

    def _format_usage(self, usage, actions, groups, prefix):
        result = super()._format_usage(usage, actions, groups, prefix)
        return result.replace("usage:", f"{self.BOLD}usage:{self.RESET}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "\033[1m\033[32mCyber Briefing Tool\033[0m"
            " — daily cybersecurity intelligence, scored and delivered to Bear"
        ),
        formatter_class=_ColouredHelp,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Full pipeline but print to stdout instead of Bear — no state changes",
    )
    parser.add_argument(
        "--gather-only",
        action="store_true",
        help="Collect from all sources, show counts, mark seen — no scoring or delivery",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics by source",
    )
    parser.add_argument(
        "--clear-source",
        metavar="SOURCE",
        help="Reset seen-state for one source (e.g. tldrsec) so it re-gathers next run",
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

    if args.clear_source:
        db_conn = get_connection()
        removed = clear_source(db_conn, args.clear_source)
        print(f"Cleared {removed} seen items for source '{args.clear_source}'")
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
