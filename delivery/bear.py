"""Bear Notes delivery module.

Creates a Bear note using the bear:// x-callback-url scheme on macOS.
Falls back to AppleScript, then to a markdown file.
"""

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger("cyberbriefing.delivery.bear")


def deliver_to_bear(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note, trying x-callback-url then AppleScript then file."""
    if sys.platform != "darwin":
        logger.warning("Not running on macOS — falling back to markdown file")
        return _write_markdown_file(title, body, tags)

    if _deliver_via_xcallback(title, body, tags):
        return True

    logger.info("x-callback-url failed, trying AppleScript")
    if _deliver_via_applescript(title, body, tags):
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
    """Create a Bear note using AppleScript via osascript."""
    try:
        tag_lines = "\n\n" + " ".join(f"#{tag}" for tag in tags)
        full_text = f"# {title}\n\n{body}{tag_lines}"
        escaped_text = full_text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'''
        tell application "Bear"
            create note with text "{escaped_text}"
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=15,
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
        return True
    except Exception as e:
        logger.error("Failed to write markdown file: %s", e)
        return False
