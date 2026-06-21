"""Weekly Cyber Summary entry point.

Reads the week's daily briefing backups, asks Claude to dedupe/rank/summarise,
and delivers a single Bear note. Mirrors the daily briefing.py: idempotent
across the 12:00/13:30 launchd pair, writes a FAILURE marker if the week is
empty, and supports --dry-run for a no-side-effects stdout preview.
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from db import state
from delivery.bear import deliver_to_bear, deliver_to_stdout
from weekly.formatter import format_weekly
from weekly.reader import read_week
from weekly.summariser import summarise_week

logger = logging.getLogger("cyberbriefing.weekly")

OUTPUT_DIR = Path(os.path.expanduser("~/cyberbriefing-output"))
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_scoring_config() -> dict:
    """Reuse the daily scoring config block (for the model name)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f).get("scoring", {})
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not load config.yaml: %s", e)
        return {}


def _write_failure(output_dir: Path, reason: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = output_dir / f"FAILURE-weekly-{today}.md"
    path.write_text(
        f"# Weekly summary FAILED — {today}\n\n{reason}\n",
        encoding="utf-8",
    )
    logger.error("Wrote %s", path)


def run_weekly(output_dir: Path, run_date: date, dry_run: bool,
               config: dict, conn) -> int:
    """Run the weekly pipeline. Returns a process exit code."""
    if not dry_run and state.was_weekly_delivered_this_week(conn):
        logger.info("Weekly summary already delivered this week — exiting cleanly")
        return 0

    stories, n_briefings, monday, sunday = read_week(output_dir, run_date)
    if not stories:
        _write_failure(output_dir,
                       "No stories found in this week's briefing backups — "
                       "every daily backup was missing or empty.")
        return 1

    try:
        summarised = summarise_week(stories, config)
    except RuntimeError as e:
        _write_failure(output_dir, f"Claude summarisation failed: {e}")
        return 1

    title, body, tags = format_weekly(
        summarised, n_briefings, len(stories), monday, sunday,
    )

    if dry_run:
        deliver_to_stdout(title, body, tags)
        return 0

    deliver_to_bear(title, body, tags)
    state.mark_weekly_delivered(conn)
    logger.info("Weekly summary delivered: %s", title)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly cyber summary")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout; no Bear, no state changes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Load .env the same way the daily job does, if present.
    load_dotenv(Path(__file__).parent / ".env")

    config = _load_scoring_config()
    conn = state.get_connection()
    return run_weekly(OUTPUT_DIR, date.today(), args.dry_run, config, conn)


if __name__ == "__main__":
    sys.exit(main())
