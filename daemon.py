"""Cyberbriefing daemon — sleeps until 06:00, runs the briefing, repeats."""

import logging
import signal
import socket
import sys
import time
from datetime import datetime, timedelta

from briefing import load_config, run_pipeline, setup_logging

BRIEFING_HOUR = 6
BRIEFING_MINUTE = 0
MAX_RETRIES = 3
RETRY_DELAY = 30
NETWORK_TIMEOUT = 3600

logger = logging.getLogger("cyberbriefing.daemon")


def _seconds_until_next_run() -> float:
    """Return seconds until the next 06:00."""
    now = datetime.now()
    target = now.replace(hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _wait_for_network():
    """Block until we can open a TCP socket, up to NETWORK_TIMEOUT seconds."""
    deadline = time.monotonic() + NETWORK_TIMEOUT
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            s = socket.create_connection(("www.google.com", 443), timeout=5)
            s.close()
            logger.info("Network available (attempt %d).", attempt)
            return True
        except OSError:
            time.sleep(5)
    logger.error("Network unavailable after %ds — skipping today's briefing.", NETWORK_TIMEOUT)
    return False


def _run_briefing():
    """Wait for network, then run the pipeline with retries."""
    if not _wait_for_network():
        return

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
    logger.info("Daemon started. Briefing scheduled daily at %02d:%02d.", BRIEFING_HOUR, BRIEFING_MINUTE)

    # Graceful shutdown on SIGTERM/SIGINT.
    def _shutdown(signum, frame):
        logger.info("Received signal %d — shutting down.", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        wait = _seconds_until_next_run()
        hours, remainder = divmod(int(wait), 3600)
        minutes = remainder // 60
        logger.info("Next briefing in %dh %dm. Sleeping until %02d:%02d.",
                     hours, minutes, BRIEFING_HOUR, BRIEFING_MINUTE)
        time.sleep(wait)
        _run_briefing()


if __name__ == "__main__":
    main()
