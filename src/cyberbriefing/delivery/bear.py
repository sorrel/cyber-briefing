"""Bear Notes delivery module.

Bear 2.x exposes no AppleScript scripting interface (no .sdef, no
OSAScriptingDefinition in Info.plist — `sdef` returns -192). The only
programmatic path is the bear:// x-callback-url scheme.

Delivery strategy:
  1. If Bear is running, send the URL straight away.
  2. If Bear is not running, launch it, wait until the process has been
     alive long enough to be past its startup race, then send the URL.

The markdown backup is written by the dispatcher (delivery/dispatch.py) for
every delivery method, so this module is Bear-only.
"""

import logging
import subprocess
import sys
import time
from urllib.parse import quote

# How long to wait for a cold-launched Bear to settle before sending the URL.
# `open` returns immediately when the OS hands the URL off, so if we fire too
# early the URL is dropped on the floor during Bear's startup.
_BEAR_LAUNCH_TIMEOUT_S = 15
_BEAR_LAUNCH_SETTLE_S = 2

logger = logging.getLogger("cyberbriefing.delivery.bear")


def _bear_is_running() -> bool:
    """Return True if Bear.app is already running."""
    result = subprocess.run(["pgrep", "-x", "Bear"], capture_output=True)
    return result.returncode == 0


def _launch_bear_and_wait() -> bool:
    """Launch Bear in the background and wait for it to be ready.

    Returns True once Bear has been alive for at least _BEAR_LAUNCH_SETTLE_S
    seconds, False if it doesn't come up within _BEAR_LAUNCH_TIMEOUT_S.
    """
    try:
        subprocess.run(["open", "-ga", "Bear"], capture_output=True, timeout=10)
    except Exception as e:
        logger.warning("Failed to launch Bear: %s", e)
        return False

    deadline = time.monotonic() + _BEAR_LAUNCH_TIMEOUT_S
    first_seen: float | None = None
    while time.monotonic() < deadline:
        if _bear_is_running():
            if first_seen is None:
                first_seen = time.monotonic()
            if time.monotonic() - first_seen >= _BEAR_LAUNCH_SETTLE_S:
                return True
        else:
            first_seen = None
        time.sleep(0.5)
    logger.warning("Bear did not come up within %ds", _BEAR_LAUNCH_TIMEOUT_S)
    return False


def deliver_to_bear(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note via x-callback-url. Returns True iff Bear accepted it.

    The markdown backup is written by the dispatcher (delivery/dispatch.py) for
    every method, so this function is now Bear-only.
    """
    if sys.platform != "darwin":
        logger.warning("Not running on macOS — cannot deliver to Bear")
        return False

    if not _bear_is_running():
        logger.info("Bear not running — launching and waiting for it to settle")
        if not _launch_bear_and_wait():
            logger.warning("Could not bring Bear up")
            return False

    return _deliver_via_xcallback(title, body, tags)


def deliver_to_stdout(title: str, body: str, tags: list[str]) -> bool:
    """Print the briefing to stdout (for --dry-run mode)."""
    tag_line = " ".join(f"#{tag}" for tag in tags)
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"  Tags: {tag_line}")
    print(f"{'=' * 60}\n")
    print(body)
    print(f"\n{'=' * 60}\n")
    return True


def _deliver_via_xcallback(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note using the x-callback-url scheme."""
    try:
        tag_string = ",".join(tags)
        url = (
            f"bear://x-callback-url/create"
            f"?title={quote(title)}"
            f"&text={quote(body)}"
            f"&tags={quote(tag_string)}"
            f"&open_note=no"
        )
        result = subprocess.run(
            ["open", url], capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("Delivered to Bear via x-callback-url: %s", title)
            return True
        else:
            logger.warning("x-callback-url code %d: %s", result.returncode, result.stderr)
            return False
    except subprocess.TimeoutExpired:
        logger.warning("x-callback-url timed out")
        return False
    except Exception as e:
        logger.warning("x-callback-url failed: %s", e)
        return False
