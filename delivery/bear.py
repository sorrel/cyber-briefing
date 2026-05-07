"""Bear Notes delivery module.

Creates a Bear note using the bear:// x-callback-url scheme on macOS.
Falls back to AppleScript, then to a markdown file.
"""

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MARKDOWN_RETENTION_DAYS = 7

logger = logging.getLogger("cyberbriefing.delivery.bear")


def _bear_is_running() -> bool:
    """Return True if Bear.app is already running."""
    result = subprocess.run(["pgrep", "-x", "Bear"], capture_output=True)
    return result.returncode == 0


def deliver_to_bear(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note, trying x-callback-url then AppleScript then file.

    x-callback-url via `open` only works reliably when Bear is already running —
    it returns exit code 0 immediately without waiting for Bear to process the URL.
    When Bear is not running we skip straight to AppleScript, which launches the
    app and waits for it to be ready before executing the command.

    A markdown backup is always written regardless of Bear delivery outcome, so
    the briefing is never silently lost if Bear fails at 06:00.
    """
    if sys.platform != "darwin":
        logger.warning("Not running on macOS — falling back to markdown file")
        return _write_markdown_file(title, body, tags)

    bear_running = _bear_is_running()

    if bear_running and _deliver_via_xcallback(title, body, tags):
        _write_markdown_file(title, body, tags)
        return True

    if not bear_running:
        logger.info("Bear not running — skipping x-callback-url, using AppleScript directly")

    if _deliver_via_applescript(title, body, tags):
        _write_markdown_file(title, body, tags)
        return True

    logger.info("AppleScript failed, falling back to markdown file")
    return _write_markdown_file(title, body, tags)


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


def _deliver_via_applescript(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note using AppleScript via osascript.

    Text is passed as an argv argument rather than interpolated into the
    script string, avoiding any AppleScript injection from feed content.
    """
    try:
        tag_lines = "\n\n" + " ".join(f"#{tag}" for tag in tags)
        full_text = f"# {title}\n\n{body}{tag_lines}"
        script = 'on run argv\ntell application "Bear"\ncreate note with text (item 1 of argv)\nend tell\nend run'
        result = subprocess.run(
            ["osascript", "-e", script, "--", full_text],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("Delivered to Bear via AppleScript: %s", title)
            return True
        else:
            logger.warning("AppleScript code %d: %s", result.returncode, result.stderr)
            return False
    except subprocess.TimeoutExpired:
        logger.warning("AppleScript timed out")
        return False
    except Exception as e:
        logger.warning("AppleScript failed: %s", e)
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
