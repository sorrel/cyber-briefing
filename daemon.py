"""Cyberbriefing daemon — sleeps until 06:00, runs the briefing, repeats."""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from briefing import load_config, run_pipeline, setup_logging

BRIEFING_HOUR = 6
BRIEFING_MINUTE = 0
MAX_RETRIES = 6
RETRY_DELAY = 300

# Flag file written before restart so launchd-restarted process runs immediately.
_RUN_NOW_FLAG = Path.home() / '.cyberbriefing' / 'run-now'

# Legacy env-var kept for backward compatibility (checked but no longer written).
_RUN_NOW_ENV = 'CYBERBRIEFING_RUN_NOW'

logger = logging.getLogger("cyberbriefing.daemon")


def _seconds_until_next_run() -> float:
    """Return seconds until the next 06:00."""
    now = datetime.now()
    target = now.replace(hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_briefing():
    """Run the pipeline with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Briefing attempt %d/%d.", attempt, MAX_RETRIES)
            config = load_config()
            success = run_pipeline(config)
            if success:
                logger.info("Briefing delivered successfully.")
                return
            logger.warning("Briefing pipeline returned failure.")
        except Exception:
            logger.exception("Briefing pipeline crashed on attempt %d:", attempt)

        if attempt < MAX_RETRIES:
            logger.info("Retrying in %ds...", RETRY_DELAY)
            time.sleep(RETRY_DELAY)


def main():
    setup_logging(verbose=False)

    run_now = bool(os.environ.pop(_RUN_NOW_ENV, None))
    if not run_now and _RUN_NOW_FLAG.exists():
        try:
            _RUN_NOW_FLAG.unlink()
        except OSError:
            pass
        run_now = True

    def _shutdown(signum, frame):
        logger.info("Received signal %d — shutting down.", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if run_now:
        logger.info("Daemon restarted for immediate run.")
    else:
        logger.info("Daemon started. Briefing scheduled daily at %02d:%02d.", BRIEFING_HOUR, BRIEFING_MINUTE)
        wait = _seconds_until_next_run()
        hours, remainder = divmod(int(wait), 3600)
        minutes = remainder // 60
        logger.info("Next briefing in %dh %dm. Sleeping until %02d:%02d.",
                     hours, minutes, BRIEFING_HOUR, BRIEFING_MINUTE)
        time.sleep(wait)

    _run_briefing()
    sys.exit(0)


if __name__ == "__main__":
    main()
