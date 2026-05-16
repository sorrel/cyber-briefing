"""Bear Notes delivery module.

Bear 2.x exposes no AppleScript scripting interface (no .sdef, no
OSAScriptingDefinition in Info.plist — `sdef` returns -192). The only
programmatic path is the bear:// x-callback-url scheme.

Delivery strategy:
  1. If Bear is running, send the URL straight away.
  2. If Bear is not running, launch it, wait until the process has been
     alive long enough to be past its startup race, then send the URL.
  3. Always write a markdown backup so the briefing is never lost — `open`
     returns 0 the instant the OS accepts the URL handoff, so we cannot
     tell if Bear actually consumed it (e.g. if Bear was shutting down for
     a macOS update, as happened on 16 May 2026).
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

MARKDOWN_RETENTION_DAYS = 7

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
    """Create a Bear note via x-callback-url; always write a markdown backup.

    The markdown backup is the only thing that survives if Bear silently
    drops the URL (e.g. shutting down for an OS update). It is written
    regardless of whether the x-callback-url call appears to succeed.
    """
    if sys.platform != "darwin":
        logger.warning("Not running on macOS — falling back to markdown file")
        return _write_markdown_file(title, body, tags)

    if not _bear_is_running():
        logger.info("Bear not running — launching and waiting for it to settle")
        if not _launch_bear_and_wait():
            logger.warning("Could not bring Bear up; relying on markdown backup")
            return _write_markdown_file(title, body, tags)

    bear_ok = _deliver_via_xcallback(title, body, tags)
    backup_ok = _write_markdown_file(title, body, tags)
    if not bear_ok:
        logger.warning("Bear x-callback-url returned an error — relying on markdown backup")
    # Success means "today's briefing is preserved somewhere". The markdown
    # backup is enough on its own; the 06:17 / 07:30 launchd pair already gives
    # us a second attempt at Bear delivery on bad mornings.
    return backup_ok


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


def _write_markdown_file(title: str, body: str, tags: list[str]) -> bool:
    """Write the briefing as a markdown file as a last resort."""
    try:
        output_dir = Path(os.path.expanduser("~/cyberbriefing-output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        filepath = output_dir / f"{safe_name}.md"
        tag_line = " ".join(f"#{tag}" for tag in tags)
        content = f"# {title}\n\n{tag_line}\n\n{body}"
        filepath.write_text(content, encoding="utf-8")
        logger.info("Wrote markdown fallback to %s", filepath)
        _prune_old_markdown_files(output_dir)
        return True
    except Exception as e:
        logger.error("Failed to write markdown file: %s", e)
        return False


def _prune_old_markdown_files(output_dir: Path) -> None:
    """Delete .md files older than MARKDOWN_RETENTION_DAYS by mtime.

    Bear is the canonical store; this directory is a safety net for the case
    where Bear delivery silently fails. Bounded retention keeps it from
    growing unbounded.
    """
    cutoff = time.time() - MARKDOWN_RETENTION_DAYS * 86400
    for path in output_dir.glob("*.md"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logger.info("Pruned old markdown backup: %s", path.name)
        except OSError as e:
            logger.warning("Failed to prune %s: %s", path, e)
