"""Cyberbriefing daemon — sleeps until 06:00, runs the briefing, repeats."""

import logging
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from briefing import load_config, run_pipeline, setup_logging

BRIEFING_HOUR = 6
BRIEFING_MINUTE = 0
MAX_RETRIES = 6
RETRY_DELAY = 300
NETWORK_INITIAL_PROBE_SECS = 30    # probe window before attempting remediation
NETWORK_POST_FLUSH_PROBE_SECS = 90  # probe window after DNS flush
# After process restart the OS network stack may still be broken (not just stale FDs).
# Sleep before probing so whatever caused the 06:00 outage can clear.
NETWORK_POST_RESTART_SLEEP_SECS = 120   # 2-minute buffer after restart
NETWORK_POST_RESTART_PROBE_SECS = 600   # 10-minute probe window in fresh process

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


def _probe_once() -> tuple[bool, str]:
    """Single AF_INET TCP probe. Returns (success, error_description)."""
    try:
        addrs = socket.getaddrinfo("www.google.com", 443, socket.AF_INET, socket.SOCK_STREAM)
        s = socket.create_connection(addrs[0][4], timeout=5)
        s.close()
        return True, ""
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_for(seconds: int) -> bool:
    """Probe repeatedly for up to `seconds`. Logs every failure at INFO."""
    deadline = time.monotonic() + seconds
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        ok, err = _probe_once()
        if ok:
            logger.info("Network available (attempt %d).", attempt)
            return True
        logger.info("Network probe %d failed: %s", attempt, err)
        time.sleep(5)
    return False


def _flush_dns():
    """Flush the macOS DNS cache to clear stale resolver state."""
    subprocess.run(['dscacheutil', '-flushcache'], capture_output=True)
    # mDNSResponder runs as root so HUP may be silently ignored — that's fine.
    subprocess.run(['killall', '-HUP', 'mDNSResponder'], capture_output=True)
    logger.info("DNS flush attempted (dscacheutil + mDNSResponder HUP).")


def _restart_for_fresh_state():
    """Exit so launchd restarts with fresh FDs; flag file ensures immediate run."""
    logger.info("Restarting daemon process to clear stale network state.")
    # os.execve is unreliable when launched via 'uv run' (argv/cwd ambiguity,
    # and if FDs are already EBADF the exec itself may fail silently).
    # A flag file survives any launchd restart cleanly.
    try:
        _RUN_NOW_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _RUN_NOW_FLAG.touch()
    except OSError:
        pass  # If we can't write the flag, the restart still gives fresh FDs
    sys.exit(0)


def _wait_for_network(allow_remediation: bool) -> bool:
    """
    Probe network, with active remediation on first failure.

    First run (allow_remediation=True):
    - Probes 30s; on failure flushes DNS, waits 10s, probes 90s more.
    - If still failing: restarts the process (flag file → launchd restart).

    Post-restart run (allow_remediation=False):
    - The OS network stack itself may be broken (not just stale FDs in this process).
    - Sleeps 2 minutes to let whatever caused the outage clear, then probes for 10 minutes.
    - Gives up only if network is still absent after that window.
    """
    logger.info("Probing network (initial %ds window).", NETWORK_INITIAL_PROBE_SECS)
    if _probe_for(NETWORK_INITIAL_PROBE_SECS):
        return True

    if not allow_remediation:
        # A fresh process restart did not clear the EBADF — the OS network stack is broken.
        # Wait for it to recover rather than giving up immediately.
        logger.warning(
            "Network unavailable in fresh process. Waiting %ds for OS stack to recover.",
            NETWORK_POST_RESTART_SLEEP_SECS,
        )
        time.sleep(NETWORK_POST_RESTART_SLEEP_SECS)
        logger.info("Probing network post-restart (%ds window).", NETWORK_POST_RESTART_PROBE_SECS)
        if _probe_for(NETWORK_POST_RESTART_PROBE_SECS):
            return True
        logger.error("Network still unavailable after restart — skipping today's briefing.")
        return False

    logger.warning("Network unavailable after %ds — flushing DNS cache.", NETWORK_INITIAL_PROBE_SECS)
    _flush_dns()
    time.sleep(10)  # give mDNSResponder time to restart

    logger.info("Retrying network probe (%ds window).", NETWORK_POST_FLUSH_PROBE_SECS)
    if _probe_for(NETWORK_POST_FLUSH_PROBE_SECS):
        return True

    # DNS flush didn't help — restart for a truly fresh process state.
    _restart_for_fresh_state()
    return False  # unreachable


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
        logger.info("Daemon restarted for immediate run (fresh process state).")
    else:
        logger.info("Daemon started. Briefing scheduled daily at %02d:%02d.", BRIEFING_HOUR, BRIEFING_MINUTE)
        wait = _seconds_until_next_run()
        hours, remainder = divmod(int(wait), 3600)
        minutes = remainder // 60
        logger.info("Next briefing in %dh %dm. Sleeping until %02d:%02d.",
                     hours, minutes, BRIEFING_HOUR, BRIEFING_MINUTE)
        time.sleep(wait)

    if not _wait_for_network(allow_remediation=not run_now):
        sys.exit(1)

    _run_briefing()
    sys.exit(0)


if __name__ == "__main__":
    main()
