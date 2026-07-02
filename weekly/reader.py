"""Read and parse the week's daily briefing markdown backups.

The daily pipeline writes one markdown backup per day to
~/cyberbriefing-output/ named "Cyber Briefing _ YYYY-MM-DD.md". This module
selects the Monday→Sunday window for a given run date, parses each file into
its curated news stories, and excludes the "🔒 Vulnerabilities" section (the
published CVEs) entirely.
"""

import logging
import re
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("cyberbriefing.weekly.reader")

_FILENAME_RE = re.compile(r"^Cyber Briefing _ (\d{4}-\d{2}-\d{2})\.md$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_SCORE_RE = re.compile(r"\*Score:\s*([\d.]+)")

# Maps the emoji section header to a short section label. The Vulnerabilities
# section is deliberately absent so it is skipped.
_SECTION_LABELS = {
    "🔴 Critical": "Critical",
    "🟡 Notable": "Notable",
    "📋 On your radar": "Radar",
    "🇬🇧 Britain": "Britain",
}


def _section_label(header: str) -> str | None:
    """Return the short label for a '## ...' header, or None to skip it."""
    text = header.lstrip("#").strip()
    if "Vulnerabilities" in text:
        return None
    for prefix, label in _SECTION_LABELS.items():
        if text.startswith(prefix):
            return label
    return None


def _parse_standard_block(block: str, section: str, date_str: str) -> dict | None:
    """Parse a '### ' story block into a Story dict."""
    lines = block.splitlines()
    headline = lines[0].strip()
    if not headline:
        return None
    sources: list[tuple[str, str]] = []
    paragraph_parts: list[str] = []
    score: float | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        score_match = _SCORE_RE.search(stripped)
        links = _LINK_RE.findall(stripped)
        if links and stripped.startswith("["):
            sources.extend((name, url) for name, url in links)
        elif score_match:
            score = float(score_match.group(1))
        elif not stripped.startswith("*"):
            paragraph_parts.append(stripped)
    return {
        "date": date_str,
        "section": section,
        "headline": headline,
        "sources": sources,
        "paragraph": " ".join(paragraph_parts),
        "score": score,
    }


def _parse_britain_bullet(line: str, date_str: str) -> dict | None:
    """Parse a Britain '- [headline](url) · *source*' bullet into a Story."""
    body = line.lstrip("-").strip()
    links = _LINK_RE.findall(body)
    source_match = re.search(r"\*([^*]+)\*\s*$", body)
    source_name = source_match.group(1).strip() if source_match else ""
    if links:
        headline, url = links[0]
    else:
        # Headline-only bullet with no link.
        headline = re.sub(r"·\s*\*[^*]+\*\s*$", "", body).strip()
        url = ""
    if not headline:
        return None
    return {
        "date": date_str,
        "section": "Britain",
        "headline": headline,
        "sources": [(source_name, url)] if source_name or url else [],
        "paragraph": "",
        "score": None,
    }


def parse_briefing_text(text: str, date_str: str) -> list[dict]:
    """Parse one daily briefing's markdown into a list of Story dicts.

    The Vulnerabilities section is excluded. Both standard '###' story blocks
    and Britain headline-only bullets are returned.
    """
    stories: list[dict] = []
    # Split into sections on lines beginning with '## '.
    section_label: str | None = None
    buffer: list[str] = []

    def flush(label: str | None, lines: list[str]) -> None:
        if label is None or not lines:
            return
        body = "\n".join(lines)
        if label == "Britain":
            for line in lines:
                if line.lstrip().startswith("- "):
                    story = _parse_britain_bullet(line, date_str)
                    if story:
                        stories.append(story)
            return
        # Standard sections: split on '### '.
        blocks = re.split(r"^### ", body, flags=re.MULTILINE)
        for block in blocks[1:]:
            story = _parse_standard_block(block, label, date_str)
            if story:
                stories.append(story)

    for line in text.splitlines():
        if line.startswith("## "):
            flush(section_label, buffer)
            section_label = _section_label(line)
            buffer = []
        else:
            buffer.append(line)
    flush(section_label, buffer)
    return stories


def select_week_files(output_dir: Path, run_date: date) -> tuple[list[Path], date, date]:
    """Return the backup files for the most recently completed Mon→Sun week.

    "Completed" means the week whose Sunday is the most recent Sunday on or
    before run_date. This is schedule-agnostic across both deployments:
    a Sunday run (home Mac mini, 12:00) targets the week ending that day, while
    a Monday run (work laptop, 10:00) targets the week that just ended rather
    than the empty week that starts today. Files are returned sorted
    oldest-first.
    """
    days_since_sunday = (run_date.weekday() + 1) % 7  # Mon=1 … Sat=6 … Sun=0
    sunday = run_date - timedelta(days=days_since_sunday)
    monday = sunday - timedelta(days=6)
    chosen: list[tuple[date, Path]] = []
    if output_dir.exists():
        for path in output_dir.glob("Cyber Briefing _ *.md"):
            match = _FILENAME_RE.match(path.name)
            if not match:
                continue
            file_date = date.fromisoformat(match.group(1))
            if monday <= file_date <= sunday:
                chosen.append((file_date, path))
    chosen.sort(key=lambda pair: pair[0])
    return [path for _, path in chosen], monday, sunday


def read_week(output_dir: Path, run_date: date) -> tuple[list[dict], int, date, date]:
    """Read and parse all of the week's briefings.

    Returns (stories, n_briefings, monday, sunday).
    """
    paths, monday, sunday = select_week_files(output_dir, run_date)
    stories: list[dict] = []
    for path in paths:
        match = _FILENAME_RE.match(path.name)
        date_str = match.group(1)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Could not read %s: %s", path, e)
            continue
        stories.extend(parse_briefing_text(text, date_str))
    logger.info("Read %d stories from %d briefings", len(stories), len(paths))
    return stories, len(paths), monday, sunday
