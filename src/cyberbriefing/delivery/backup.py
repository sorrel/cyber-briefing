"""Markdown backup writer for delivered briefings.

Every real delivery writes a dated markdown file to ~/cyberbriefing-output/.
This is the durable artifact: it is what the weekly pipeline reads to build the
Sunday summary, and the safety net if the primary delivery channel silently
drops the note. Extracted from delivery/bear.py so every delivery method shares
one implementation.
"""

import logging
import os
import time
from pathlib import Path

# 10 (not 7) so a slightly-late daily run or a DST shift never prunes Monday's
# backup before the Sunday-midday weekly summary reads the week.
MARKDOWN_RETENTION_DAYS = 10

logger = logging.getLogger("cyberbriefing.delivery.backup")


def write_markdown_backup(title: str, body: str, tags: list[str]) -> bool:
    """Write the briefing as a markdown file to ~/cyberbriefing-output/."""
    try:
        output_dir = Path(os.path.expanduser("~/cyberbriefing-output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        filepath = output_dir / f"{safe_name}.md"
        tag_line = " ".join(f"#{tag}" for tag in tags)
        content = f"# {title}\n\n{tag_line}\n\n{body}"
        filepath.write_text(content, encoding="utf-8")
        logger.info("Wrote markdown backup to %s", filepath)
        _prune_old_markdown_files(output_dir)
        return True
    except Exception as e:
        logger.error("Failed to write markdown backup: %s", e)
        return False


def _prune_old_markdown_files(output_dir: Path) -> None:
    """Delete .md files older than MARKDOWN_RETENTION_DAYS by mtime.

    Bounded retention keeps the safety-net directory from growing unbounded.
    """
    cutoff = time.time() - MARKDOWN_RETENTION_DAYS * 86400
    for path in output_dir.glob("*.md"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logger.info("Pruned old markdown backup: %s", path.name)
        except OSError as e:
            logger.warning("Failed to prune %s: %s", path, e)
