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
from datetime import date
from pathlib import Path

import config_loader
from db import state
from delivery.bear import deliver_to_stdout
from delivery.dispatch import deliver
from weekly.formatter import format_weekly
from weekly.reader import read_week
from weekly.summariser import summarise_week

logger = logging.getLogger("cyberbriefing.weekly")

OUTPUT_DIR = Path(os.path.expanduser("~/cyberbriefing-output"))


def _load_scoring_config() -> dict:
    """Reuse the daily scoring config block (for the model name)."""
    try:
        return config_loader.load_config().get("scoring", {})
    except OSError as e:
        logger.warning("Could not load config.yaml: %s", e)
        return {}


def _load_delivery_config() -> dict:
    """Load the delivery config block (method + slack channel).

    Routed through config_loader so a per-machine config.local.yaml (e.g. the
    laptop's delivery.method: slack) overrides the committed default.
    """
    try:
        return config_loader.load_config().get("delivery", {})
    except OSError as e:
        logger.warning("Could not load delivery config: %s", e)
        return {}


def _write_failure(output_dir: Path, run_date: date, reason: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    iso = run_date.isoformat()
    path = output_dir / f"FAILURE-weekly-{iso}.md"
    path.write_text(
        f"# Weekly summary FAILED — {iso}\n\n{reason}\n",
        encoding="utf-8",
    )
    logger.error("Wrote %s", path)


def run_weekly(output_dir: Path, run_date: date, dry_run: bool,
               config: dict, conn, delivery_cfg: dict | None = None) -> int:
    """Run the weekly pipeline. Returns a process exit code."""
    if not dry_run and state.was_weekly_delivered_this_week(conn):
        logger.info("Weekly summary already delivered this week — exiting cleanly")
        return 0

    stories, n_briefings, monday, sunday = read_week(output_dir, run_date)
    if not stories:
        _write_failure(output_dir, run_date,
                       "No stories found in this week's briefing backups — "
                       "every daily backup was missing or empty.")
        return 1

    try:
        summarised = summarise_week(stories, config)
    except RuntimeError as e:
        _write_failure(output_dir, run_date, f"Claude summarisation failed: {e}")
        return 1

    title, body, tags = format_weekly(
        summarised, n_briefings, len(stories), monday, sunday,
    )

    if dry_run:
        deliver_to_stdout(title, body, tags)
        return 0

    deliver(delivery_cfg or {}, title, body, tags)
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

    # Whole-process backstop against any hang, and a bounded .env load — the
    # secrets file is a 1Password FIFO that can block open() forever when
    # locked. Mirrors briefing.py; see CLAUDE.md (2 Jul 2026).
    watchdog = config_loader.arm_runtime_watchdog(max_seconds=900)
    env_ready = config_loader.load_env_with_timeout(Path(__file__).parent / ".env")
    if not env_ready and not args.dry_run:
        _write_failure(
            OUTPUT_DIR, date.today(),
            "1Password did not provide secrets within the timeout (local-env "
            "FIFO not fed — almost always locked). Aborted before summarising; "
            "the fallback fire will retry once 1Password is unlocked.",
        )
        return 1

    config = _load_scoring_config()
    delivery_cfg = _load_delivery_config()
    conn = state.get_connection()
    result = run_weekly(OUTPUT_DIR, date.today(), args.dry_run, config, conn, delivery_cfg)
    watchdog.cancel()
    return result


if __name__ == "__main__":
    sys.exit(main())
