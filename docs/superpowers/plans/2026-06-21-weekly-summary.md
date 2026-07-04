# Weekly Cyber Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a weekly Sunday-midday job that summarises the week's daily briefing backups into a single ranked "Weekly Cyber Summary" Bear note.

**Architecture:** A new `weekly.py` entry point plus a `weekly/` package reads the 7 daily markdown backups in `~/cyberbriefing-output/`, extracts the curated news stories (everything below the Vulnerabilities section), sends them to Claude to dedupe/rank/summarise, and delivers via the existing `delivery/bear.py`. Idempotency and a launchd plist mirror the daily job.

**Tech Stack:** Python 3, `anthropic` SDK, SQLite (`db/state.py`), pytest, launchd, `uv`.

## Global Constraints

- **British English** in all output, code comments, and the Claude prompt — copied verbatim from spec.
- **Run with `uv`**: `uv run python weekly.py ...`.
- **No new dependencies** — reuse `anthropic`, stdlib, and existing modules only.
- **Reuse unchanged**: `delivery/bear.py::deliver_to_bear`, `delivery/bear.py::deliver_to_stdout`, the Anthropic client pattern from `prioritiser/scorer.py`.
- **Data source**: daily backups at `~/cyberbriefing-output/Cyber Briefing _ YYYY-MM-DD.md`. The `## 🔒 Vulnerabilities` section is always ignored.
- **Tag**: the Bear note tag is exactly `security/briefing/weekly`.
- **Title**: `Weekly Cyber Summary — <monday> to <sunday>` using actual ISO dates, e.g. `Weekly Cyber Summary — 2026-06-15 to 2026-06-21`.
- **Tier section headers in backups** (for the parser): `## 🔒 Vulnerabilities` (ignore), `## 🔴 Critical — act on these`, `## 🟡 Notable — worth reading`, `## 📋 On your radar`, `## 🇬🇧 Britain`.

---

### Task 1: Story reader and week-window selection

**Files:**
- Create: `weekly/__init__.py` (empty)
- Create: `weekly/reader.py`
- Create: `tests/test_weekly_reader.py`

**Interfaces:**
- Consumes: nothing (entry of the pipeline).
- Produces:
  - `Story` = `dict` with keys `date: str`, `section: str`, `headline: str`, `sources: list[tuple[str, str]]`, `paragraph: str`, `score: float | None`.
  - `parse_briefing_text(text: str, date: str) -> list[Story]`
  - `select_week_files(output_dir: Path, run_date: date) -> tuple[list[Path], date, date]` — returns `(paths_sorted_oldest_first, monday, sunday)`.
  - `read_week(output_dir: Path, run_date: date) -> tuple[list[Story], int, date, date]` — returns `(stories, n_briefings, monday, sunday)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_reader.py`:

```python
from datetime import date
from pathlib import Path

from weekly.reader import parse_briefing_text, select_week_files, read_week

FIXTURE = """# Cyber Briefing — 2026-06-19

#security/briefing/daily

*3 items · Sources: BleepingComputer, CISA KEV, The Hacker News*

---

## 🔒 Vulnerabilities

### CISA KEV: Splunk file create/truncate — patch by 2026-06-21
[CISA KEV](https://nvd.nist.gov/vuln/detail/CVE-2026-20253)
*Score: 17.4 · Critical*

---

## 🔴 Critical — act on these

### F5 issues out-of-band critical patches for two NGINX RCE vulnerabilities
[The Hacker News](https://thehackernews.com/2026/06/f5.html) · [BleepingComputer](https://www.bleepingcomputer.com/news/security/f5.html)
NGINX is one of the most widely deployed web servers globally and two critical flaws enable unauthenticated RCE. Patch immediately.
*Score: 18.1*

---

## 🟡 Notable — worth reading

### Estonia to auto-quarantine all .ru inbound email to government officials
[TLDR Infosec](https://therecord.media/estonia-quarantine-russian-emails)
A notable policy measure with template value for other EU/NATO governments.
*Score: 15.7*

---

## 🇬🇧 Britain
- [ICO fines a UK firm £2m over a breach](https://ico.org.uk/action) · *ICO*
"""


def test_vulnerabilities_section_is_excluded():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    assert all("Splunk" not in s["headline"] for s in stories)
    assert all(s["section"] != "Vulnerabilities" for s in stories)


def test_standard_story_parses_all_fields():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    f5 = next(s for s in stories if s["headline"].startswith("F5 issues"))
    assert f5["date"] == "2026-06-19"
    assert f5["section"] == "Critical"
    assert f5["score"] == 18.1
    assert ("The Hacker News", "https://thehackernews.com/2026/06/f5.html") in f5["sources"]
    assert ("BleepingComputer", "https://www.bleepingcomputer.com/news/security/f5.html") in f5["sources"]
    assert "unauthenticated RCE" in f5["paragraph"]


def test_britain_bullet_parses_as_headline_only():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    ico = next(s for s in stories if "ICO fines" in s["headline"])
    assert ico["section"] == "Britain"
    assert ico["score"] is None
    assert ico["paragraph"] == ""
    assert ico["sources"] == [("ICO", "https://ico.org.uk/action")]


def test_select_week_files_picks_monday_to_sunday(tmp_path):
    # Week containing Sunday 2026-06-21 is Mon 2026-06-15 .. Sun 2026-06-21
    names = [
        "Cyber Briefing _ 2026-06-14.md",  # previous Sunday — excluded
        "Cyber Briefing _ 2026-06-15.md",  # Monday — included
        "Cyber Briefing _ 2026-06-19.md",  # included
        "Cyber Briefing _ 2026-06-21.md",  # Sunday — included
        "FAILURE-2026-06-20.md",            # not a briefing — ignored
    ]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")
    paths, monday, sunday = select_week_files(tmp_path, date(2026, 6, 21))
    got = [p.name for p in paths]
    assert got == [
        "Cyber Briefing _ 2026-06-15.md",
        "Cyber Briefing _ 2026-06-19.md",
        "Cyber Briefing _ 2026-06-21.md",
    ]
    assert monday == date(2026, 6, 15)
    assert sunday == date(2026, 6, 21)


def test_read_week_counts_briefings(tmp_path):
    (tmp_path / "Cyber Briefing _ 2026-06-19.md").write_text(FIXTURE, encoding="utf-8")
    stories, n_briefings, monday, sunday = read_week(tmp_path, date(2026, 6, 21))
    assert n_briefings == 1
    assert len(stories) == 3  # F5, Estonia, ICO — Splunk vuln excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weekly_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weekly'`.

- [ ] **Step 3: Write minimal implementation**

Create `weekly/__init__.py` as an empty file.

Create `weekly/reader.py`:

```python
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


def parse_briefing_text(text: str, date: str) -> list[dict]:
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
                    story = _parse_britain_bullet(line, date)
                    if story:
                        stories.append(story)
            return
        # Standard sections: split on '### '.
        blocks = re.split(r"^### ", body, flags=re.MULTILINE)
        for block in blocks[1:]:
            story = _parse_standard_block(block, label, date)
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
    """Return the backup files for the ISO week containing run_date.

    Monday→Sunday inclusive. Files are returned sorted oldest-first.
    """
    monday = run_date - timedelta(days=run_date.weekday())
    sunday = monday + timedelta(days=6)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weekly_reader.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add weekly/__init__.py weekly/reader.py tests/test_weekly_reader.py
git commit -m "feat(weekly): read and parse the week's briefing backups"
```

---

### Task 2: Weekly idempotency helpers in state DB

**Files:**
- Modify: `db/state.py` (add after the daily `mark_delivered_today` at `db/state.py:160`)
- Test: `tests/test_weekly_state.py`

**Interfaces:**
- Consumes: `db.state.get_connection`.
- Produces:
  - `was_weekly_delivered_this_week(conn) -> bool`
  - `mark_weekly_delivered(conn) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_state.py`:

```python
from db import state


def test_weekly_not_delivered_initially(tmp_path):
    conn = state.get_connection(str(tmp_path / "s.db"))
    assert state.was_weekly_delivered_this_week(conn) is False


def test_mark_weekly_delivered_sets_flag(tmp_path):
    conn = state.get_connection(str(tmp_path / "s.db"))
    state.mark_weekly_delivered(conn)
    assert state.was_weekly_delivered_this_week(conn) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weekly_state.py -v`
Expected: FAIL with `AttributeError: module 'db.state' has no attribute 'was_weekly_delivered_this_week'`.

- [ ] **Step 3: Write minimal implementation**

In `db/state.py`, immediately after `mark_delivered_today` (around line 160), add:

```python
# Slot used in scraper_runs to record successful weekly-summary delivery.
# Re-uses the existing table, like the daily _briefing_delivered slot.
_WEEKLY_DELIVERED_SLOT = "_weekly_delivered"


def was_weekly_delivered_this_week(conn: sqlite3.Connection) -> bool:
    """Return True if a weekly summary was already delivered this ISO week.

    Used by the launchd 13:30 fallback fire to no-op when 12:00 succeeded.
    """
    row = conn.execute(
        "SELECT last_checked FROM scraper_runs WHERE source = ?",
        (_WEEKLY_DELIVERED_SLOT,),
    ).fetchone()
    if row is None:
        return False
    last = datetime.fromisoformat(row["last_checked"]).astimezone()
    now = datetime.now().astimezone()
    return last.isocalendar()[:2] == now.isocalendar()[:2]


def mark_weekly_delivered(conn: sqlite3.Connection) -> None:
    """Record that a weekly summary was successfully delivered just now."""
    update_scraper_run(conn, _WEEKLY_DELIVERED_SLOT)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weekly_state.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add db/state.py tests/test_weekly_state.py
git commit -m "feat(weekly): add weekly delivery idempotency helpers"
```

---

### Task 3: Extend markdown backup retention

**Files:**
- Modify: `delivery/bear.py:25`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (constant change only).

- [ ] **Step 1: Make the change**

In `delivery/bear.py`, change line 25 from:

```python
MARKDOWN_RETENTION_DAYS = 7
```

to:

```python
# 10 (not 7) so a slightly-late daily run or a DST shift never prunes
# Monday's backup before the Sunday-midday weekly summary reads the week.
MARKDOWN_RETENTION_DAYS = 10
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `uv run pytest -q`
Expected: PASS (no regressions; existing suite green).

- [ ] **Step 3: Commit**

```bash
git add delivery/bear.py
git commit -m "feat(weekly): extend markdown backup retention to 10 days"
```

---

### Task 4: Weekly summariser (Claude dedupe/rank/summarise)

**Files:**
- Create: `weekly/prompt.txt`
- Create: `weekly/summariser.py`
- Test: `tests/test_weekly_summariser.py`

**Interfaces:**
- Consumes: Story dicts from `weekly.reader` (`headline`, `sources`, `paragraph`, `score`, `date`).
- Produces:
  - `SummarisedStory` = `dict` with keys `headline: str`, `sources: list[tuple[str, str]]`, `summary: str`.
  - `build_payload(stories: list[dict]) -> list[dict]` — assigns an integer `id` to each story for the Claude call.
  - `parse_response(text: str, stories: list[dict]) -> list[dict]` — parses Claude's JSON and maps `source_ids` back to the union of original `sources`.
  - `summarise_week(stories: list[dict], config: dict | None = None) -> list[dict]` — full call; returns ordered `SummarisedStory` list. Raises `RuntimeError` on API/parse failure.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_summariser.py`:

```python
from weekly.summariser import build_payload, parse_response


def _stories():
    return [
        {"date": "2026-06-19", "section": "Critical",
         "headline": "F5 NGINX RCE", "sources": [("THN", "https://thn/f5")],
         "paragraph": "Critical NGINX flaws.", "score": 18.1},
        {"date": "2026-06-20", "section": "Notable",
         "headline": "F5 NGINX patches out-of-band", "sources": [("BC", "https://bc/f5")],
         "paragraph": "Same NGINX story, day two.", "score": 16.0},
        {"date": "2026-06-18", "section": "Notable",
         "headline": "Estonia quarantines .ru email", "sources": [("TLDR", "https://tldr/ee")],
         "paragraph": "Policy measure.", "score": 15.7},
    ]


def test_build_payload_assigns_ids_and_hides_nothing_needed():
    payload = build_payload(_stories())
    assert [p["id"] for p in payload] == [0, 1, 2]
    assert payload[0]["headline"] == "F5 NGINX RCE"
    assert payload[0]["score"] == 18.1


def test_parse_response_merges_sources_by_id():
    response = """```json
{"stories": [
  {"headline": "F5 patches critical NGINX RCEs", "summary": "Patch now.", "source_ids": [0, 1]},
  {"headline": "Estonia to quarantine .ru email", "summary": "Watch this.", "source_ids": [2]}
]}
```"""
    result = parse_response(response, _stories())
    assert len(result) == 2
    assert result[0]["headline"] == "F5 patches critical NGINX RCEs"
    assert result[0]["summary"] == "Patch now."
    # Sources from both merged stories, de-duplicated, order preserved.
    assert result[0]["sources"] == [("THN", "https://thn/f5"), ("BC", "https://bc/f5")]
    assert result[1]["sources"] == [("TLDR", "https://tldr/ee")]


def test_parse_response_skips_unknown_ids():
    response = '{"stories": [{"headline": "H", "summary": "S", "source_ids": [0, 99]}]}'
    result = parse_response(response, _stories())
    assert result[0]["sources"] == [("THN", "https://thn/f5")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weekly_summariser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weekly.summariser'`.

- [ ] **Step 3: Write minimal implementation**

Create `weekly/prompt.txt`:

```
You are curating a weekly cybersecurity summary for a UK-based application
security professional. You will receive a JSON array of news stories gathered
from this week's daily briefings. Each story has an integer "id", a
"headline", the "sources" that covered it, a "paragraph" explaining why it
matters, a "score" (the daily briefing's importance score, higher is more
important; may be null), and the "date" it appeared.

Your job:

1. DE-DUPLICATE. The same story often recurs across several days, sometimes
   under different headlines or from different sources. Merge those into one
   entry. When you merge, list every contributing story's id in "source_ids".

2. RANK by importance. Use the "score" where present, and prioritise active
   exploitation and critical items. Give higher priority to blogs, cheat
   sheets, and new tool capabilities — these are especially valuable.

3. SELECT the most important stories of the week — roughly the top 8 to 12.

4. SUMMARISE each selected story in 1–2 sentences explaining why it matters.
   Write a clear, self-contained headline. Use British English throughout.

Do NOT invent stories. Only use what you are given.

Return ONLY valid JSON, no prose, in exactly this shape:

{"stories": [
  {"headline": "string", "summary": "string", "source_ids": [int, ...]}
]}

Order the array from most important to least important.
```

Create `weekly/summariser.py`:

```python
"""Send the week's stories to Claude to dedupe, rank, and summarise.

Mirrors the client/parse pattern of prioritiser/scorer.py: model from config,
ANTHROPIC_API_KEY from the environment, a single messages.create call with the
system prompt cached, and ```json fence stripping before json.loads.
"""

import json
import logging
import os
from pathlib import Path

import anthropic

logger = logging.getLogger("cyberbriefing.weekly.summariser")

PROMPT_PATH = Path(__file__).parent / "prompt.txt"
MAX_TOKENS = 8000


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_payload(stories: list[dict]) -> list[dict]:
    """Attach a stable integer id to each story for the Claude call."""
    payload = []
    for i, story in enumerate(stories):
        payload.append({
            "id": i,
            "date": story["date"],
            "section": story["section"],
            "headline": story["headline"],
            "sources": [name for name, _ in story["sources"]],
            "paragraph": story["paragraph"],
            "score": story["score"],
        })
    return payload


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def parse_response(text: str, stories: list[dict]) -> list[dict]:
    """Parse Claude's JSON and map source_ids back to original sources.

    Raises ValueError on JSON parse failure.
    """
    result = json.loads(_strip_fences(text))
    summarised: list[dict] = []
    for entry in result.get("stories", []):
        sources: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for sid in entry.get("source_ids", []):
            if 0 <= sid < len(stories):
                for src in stories[sid]["sources"]:
                    if src not in seen:
                        seen.add(src)
                        sources.append(src)
        summarised.append({
            "headline": entry.get("headline", "").strip(),
            "summary": entry.get("summary", "").strip(),
            "sources": sources,
        })
    return summarised


def summarise_week(stories: list[dict], config: dict | None = None) -> list[dict]:
    """Dedupe, rank, and summarise the week. Raises RuntimeError on failure."""
    config = config or {}
    model = config.get("model", "claude-sonnet-4-6")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot summarise week")

    system_prompt = load_prompt()
    payload = build_payload(stories)
    user_message = (
        f"Here are {len(payload)} cybersecurity stories from this week's "
        f"briefings. Produce the weekly summary.\n\n"
        + json.dumps(payload, indent=None)
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API call failed: {e}") from e

    response_text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    try:
        summarised = parse_response(response_text, stories)
    except ValueError as e:
        logger.debug("Raw response snippet: %s", response_text[:500])
        raise RuntimeError(f"Could not parse Claude response: {e}") from e

    if not summarised:
        raise RuntimeError("Claude returned no stories")
    logger.info("Claude summarised %d stories from %d inputs", len(summarised), len(stories))
    return summarised
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weekly_summariser.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add weekly/prompt.txt weekly/summariser.py tests/test_weekly_summariser.py
git commit -m "feat(weekly): add Claude summariser for the weekly digest"
```

---

### Task 5: Weekly markdown formatter

**Files:**
- Create: `weekly/formatter.py`
- Test: `tests/test_weekly_formatter.py`

**Interfaces:**
- Consumes: `SummarisedStory` dicts from `weekly.summariser`.
- Produces:
  - `format_weekly(summarised: list[dict], n_briefings: int, n_stories: int, monday: date, sunday: date) -> tuple[str, str, list[str]]` — returns `(title, body, tags)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_formatter.py`:

```python
from datetime import date

from weekly.formatter import format_weekly


def test_format_weekly_builds_title_body_tags():
    summarised = [
        {"headline": "F5 patches critical NGINX RCEs",
         "summary": "Unauthenticated RCE in a ubiquitous web server. Patch now.",
         "sources": [("The Hacker News", "https://thn/f5"), ("BleepingComputer", "https://bc/f5")]},
        {"headline": "Estonia to quarantine .ru email",
         "summary": "A policy template for other EU/NATO governments.",
         "sources": [("TLDR Infosec", "https://tldr/ee")]},
    ]
    title, body, tags = format_weekly(
        summarised, n_briefings=7, n_stories=42,
        monday=date(2026, 6, 15), sunday=date(2026, 6, 21),
    )
    assert title == "Weekly Cyber Summary — 2026-06-15 to 2026-06-21"
    assert tags == ["security/briefing/weekly"]
    assert not body.lstrip().startswith("#")  # no title heading
    assert "*Reviewed 7 briefings · 42 stories this week.*" in body
    assert "### F5 patches critical NGINX RCEs" in body
    assert "[The Hacker News](https://thn/f5) · [BleepingComputer](https://bc/f5)" in body
    assert "Unauthenticated RCE in a ubiquitous web server. Patch now." in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weekly_formatter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weekly.formatter'`.

- [ ] **Step 3: Write minimal implementation**

Create `weekly/formatter.py`:

```python
"""Format the summarised week into a Bear-ready markdown note.

No title heading is emitted in the body — Bear takes the title from the
x-callback-url title parameter, and the markdown backup adds its own heading.
"""

from datetime import date

WEEKLY_TAG = "security/briefing/weekly"


def format_weekly(
    summarised: list[dict],
    n_briefings: int,
    n_stories: int,
    monday: date,
    sunday: date,
) -> tuple[str, str, list[str]]:
    """Return (title, body, tags) for the weekly summary note."""
    title = f"Weekly Cyber Summary — {monday.isoformat()} to {sunday.isoformat()}"

    lines = [
        f"*Reviewed {n_briefings} briefings · {n_stories} stories this week.*",
        "",
    ]
    for story in summarised:
        lines.append(f"### {story['headline']}")
        source_links = [f"[{name}]({url})" for name, url in story["sources"] if url]
        if source_links:
            lines.append(" · ".join(source_links))
        if story["summary"]:
            lines.append(story["summary"])
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    return title, body, [WEEKLY_TAG]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weekly_formatter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add weekly/formatter.py tests/test_weekly_formatter.py
git commit -m "feat(weekly): format the weekly summary markdown"
```

---

### Task 6: Entry point and pipeline wiring

**Files:**
- Create: `weekly_run.py` (the entry point is named `weekly_run.py`, **not** `weekly.py`, so it does not clash with the `weekly/` package import name)
- Test: `tests/test_weekly_pipeline.py`

**Interfaces:**
- Consumes: `weekly.reader.read_week`, `weekly.summariser.summarise_week`, `weekly.formatter.format_weekly`, `delivery.bear.deliver_to_bear`, `delivery.bear.deliver_to_stdout`, `db.state` weekly helpers.
- Produces:
  - `run_weekly(output_dir: Path, run_date: date, dry_run: bool, config: dict, conn) -> int` — returns a process exit code (0 success, 1 failure).
  - `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_pipeline.py`. These tests inject fakes for the Claude call and Bear delivery so no network or Bear is touched.

```python
from datetime import date
from pathlib import Path

import weekly_run as weekly_mod
from db import state

FIXTURE = """# Cyber Briefing — 2026-06-19

## 🔴 Critical — act on these

### F5 NGINX RCE
[THN](https://thn/f5)
Critical NGINX flaws. Patch now.
*Score: 18.1*
"""


def _write_week(tmp_path):
    (tmp_path / "Cyber Briefing _ 2026-06-19.md").write_text(FIXTURE, encoding="utf-8")


def test_run_weekly_dry_run_makes_no_state_changes(tmp_path, monkeypatch, capsys):
    _write_week(tmp_path)
    conn = state.get_connection(str(tmp_path / "s.db"))
    monkeypatch.setattr(
        weekly_mod, "summarise_week",
        lambda stories, config: [
            {"headline": "F5 patches NGINX", "summary": "Patch now.",
             "sources": [("THN", "https://thn/f5")]}
        ],
    )
    rc = weekly_mod.run_weekly(tmp_path, date(2026, 6, 21), dry_run=True, config={}, conn=conn)
    assert rc == 0
    assert "F5 patches NGINX" in capsys.readouterr().out
    assert state.was_weekly_delivered_this_week(conn) is False  # dry-run: no mark


def test_run_weekly_real_marks_delivered(tmp_path, monkeypatch):
    _write_week(tmp_path)
    conn = state.get_connection(str(tmp_path / "s.db"))
    monkeypatch.setattr(
        weekly_mod, "summarise_week",
        lambda stories, config: [
            {"headline": "F5 patches NGINX", "summary": "Patch now.",
             "sources": [("THN", "https://thn/f5")]}
        ],
    )
    delivered = {}
    monkeypatch.setattr(
        weekly_mod, "deliver_to_bear",
        lambda title, body, tags: delivered.update(title=title, body=body, tags=tags) or True,
    )
    rc = weekly_mod.run_weekly(tmp_path, date(2026, 6, 21), dry_run=False, config={}, conn=conn)
    assert rc == 0
    assert delivered["title"] == "Weekly Cyber Summary — 2026-06-15 to 2026-06-21"
    assert state.was_weekly_delivered_this_week(conn) is True


def test_run_weekly_no_stories_writes_failure(tmp_path, monkeypatch):
    # Empty week → FAILURE file, non-zero exit.
    conn = state.get_connection(str(tmp_path / "s.db"))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rc = weekly_mod.run_weekly(out_dir, date(2026, 6, 21), dry_run=False, config={}, conn=conn)
    assert rc == 1
    failures = list(out_dir.glob("FAILURE-weekly-*.md"))
    assert len(failures) == 1


def test_run_weekly_idempotent_when_already_delivered(tmp_path, monkeypatch):
    _write_week(tmp_path)
    conn = state.get_connection(str(tmp_path / "s.db"))
    state.mark_weekly_delivered(conn)
    called = {"summarise": False}
    monkeypatch.setattr(
        weekly_mod, "summarise_week",
        lambda stories, config: called.update(summarise=True) or [],
    )
    rc = weekly_mod.run_weekly(tmp_path, date(2026, 6, 21), dry_run=False, config={}, conn=conn)
    assert rc == 0
    assert called["summarise"] is False  # short-circuited before any work
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weekly_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weekly_run'`.

- [ ] **Step 3: Write minimal implementation**

Create `weekly_run.py`:

```python
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
from datetime import date, datetime
from pathlib import Path

import yaml

from db import state
from delivery.bear import deliver_to_bear, deliver_to_stdout
from weekly.formatter import format_weekly
from weekly.reader import read_week
from weekly.summariser import summarise_week

logger = logging.getLogger("cyberbriefing.weekly")

OUTPUT_DIR = Path(os.path.expanduser("~/cyberbriefing-output"))
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_scoring_config() -> dict:
    """Reuse the daily scoring config block (for the model name)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f).get("scoring", {})
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not load config.yaml: %s", e)
        return {}


def _write_failure(output_dir: Path, reason: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = output_dir / f"FAILURE-weekly-{today}.md"
    path.write_text(
        f"# Weekly summary FAILED — {today}\n\n{reason}\n",
        encoding="utf-8",
    )
    logger.error("Wrote %s", path)


def run_weekly(output_dir: Path, run_date: date, dry_run: bool,
               config: dict, conn) -> int:
    """Run the weekly pipeline. Returns a process exit code."""
    if not dry_run and state.was_weekly_delivered_this_week(conn):
        logger.info("Weekly summary already delivered this week — exiting cleanly")
        return 0

    stories, n_briefings, monday, sunday = read_week(output_dir, run_date)
    if not stories:
        _write_failure(output_dir,
                       "No stories found in this week's briefing backups — "
                       "every daily backup was missing or empty.")
        return 1

    try:
        summarised = summarise_week(stories, config)
    except RuntimeError as e:
        _write_failure(output_dir, f"Claude summarisation failed: {e}")
        return 1

    title, body, tags = format_weekly(
        summarised, n_briefings, len(stories), monday, sunday,
    )

    if dry_run:
        deliver_to_stdout(title, body, tags)
        return 0

    deliver_to_bear(title, body, tags)
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

    # Load .env the same way the daily job does, if present.
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    config = _load_scoring_config()
    conn = state.get_connection()
    return run_weekly(OUTPUT_DIR, date.today(), args.dry_run, config, conn)


if __name__ == "__main__":
    sys.exit(main())
```

> **`.env` note:** Verify how `briefing.py` loads `.env` before finalising — if it uses `python-dotenv` or a shared helper, reuse that exact mechanism instead of the inline parser above to stay DRY. Adjust this block to match.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weekly_pipeline.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the whole suite and a real dry-run**

Run: `uv run pytest -q`
Expected: PASS (all tests green).

Run: `uv run python weekly_run.py --dry-run`
Expected: prints a "Weekly Cyber Summary — …" note to stdout built from the real backups in `~/cyberbriefing-output/` (requires `ANTHROPIC_API_KEY`); no state changes.

- [ ] **Step 6: Commit**

```bash
git add weekly_run.py tests/test_weekly_pipeline.py
git commit -m "feat(weekly): add entry point wiring the weekly pipeline"
```

---

### Task 7: launchd plist for Sunday scheduling

**Files:**
- Create: `com.cyberbriefing.weekly.plist`

**Interfaces:**
- Consumes: `weekly_run.py`.
- Produces: nothing (deployment artifact).

- [ ] **Step 1: Inspect the daily plist to copy its hardening verbatim**

Run: `cat com.cyberbriefing.daily.plist`
Expected: shows `LimitLoadToSessionType=Aqua`, `ProcessType=Interactive`, `RunAtLoad=false`, the `caffeinate -is` wrapper, and the exact `uv` program path. Copy the `ProgramArguments` shape (interpreter path, working directory) exactly — do not guess paths.

- [ ] **Step 2: Create the weekly plist**

Create `com.cyberbriefing.weekly.plist`, identical in structure to the daily plist but with: `Label` = `com.cyberbriefing.weekly`; `ProgramArguments` pointing at `weekly_run.py` instead of `briefing.py`; logs to `/tmp/cyberbriefing-weekly.log` and `/tmp/cyberbriefing-weekly.err`; and `StartCalendarInterval` as an array of two dicts — `{Weekday=0, Hour=12, Minute=0}` and `{Weekday=0, Hour=13, Minute=30}` (launchd Weekday 0 = Sunday). Keep `LimitLoadToSessionType=Aqua`, `ProcessType=Interactive`, `RunAtLoad=false`, and the `caffeinate -is` wrapper from the daily plist.

- [ ] **Step 3: Validate the plist parses**

Run: `plutil -lint com.cyberbriefing.weekly.plist`
Expected: `com.cyberbriefing.weekly.plist: OK`.

- [ ] **Step 4: Commit**

```bash
git add com.cyberbriefing.weekly.plist
git commit -m "feat(weekly): add Sunday launchd plist (12:00 + 13:30 fallback)"
```

---

### Task 8: Document the weekly job in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (docs).

- [ ] **Step 1: Add a "Weekly summary" section**

In `CLAUDE.md`, after the `## Scheduling` section, add a `## Weekly summary` section documenting:
- What it is: reads the week's daily backups in `~/cyberbriefing-output/`, ignores the Vulnerabilities section, sends curated stories to Claude to dedupe/rank/summarise, delivers a `Weekly Cyber Summary — <Mon> to <Sun>` Bear note tagged `security/briefing/weekly`.
- Running it: `uv run python weekly_run.py --dry-run` and `uv run python weekly_run.py`.
- Architecture: `weekly_run.py` entry point + `weekly/` package (`reader.py`, `summariser.py`, `prompt.txt`, `formatter.py`); reuses `delivery/bear.py` and `db/state.py`.
- Scheduling: `com.cyberbriefing.weekly.plist`, Sunday 12:00 primary + 13:30 idempotent fallback (`was_weekly_delivered_this_week`); same Aqua/Interactive/`caffeinate` hardening as the daily; no `pmset` wake needed at midday. Include the install commands (bootout/cp/bootstrap, adapted from the daily section) and log paths `/tmp/cyberbriefing-weekly.{log,err}`.
- Failure mode: `FAILURE-weekly-<date>.md` written if the week's backups are empty or Claude fails.
- Note: markdown backup retention was raised 7 → 10 days so Sunday always sees the full week.

- [ ] **Step 2: Install and smoke-test the schedule (manual, on the target machine)**

Run:
```bash
cp com.cyberbriefing.weekly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.weekly.plist
launchctl print gui/$(id -u)/com.cyberbriefing.weekly
launchctl kickstart -k gui/$(id -u)/com.cyberbriefing.weekly
```
Expected: `launchctl print` shows the job loaded with `Aqua`/`Interactive`; the kickstart produces a "Weekly Cyber Summary" note in Bear and a backup in `~/cyberbriefing-output/`; `/tmp/cyberbriefing-weekly.log` shows a clean run.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(weekly): document the weekly summary job and scheduling"
```

---

## Self-Review

**Spec coverage:**
- Data source = daily backups → Task 1 (`reader.py`). ✓
- Ignore Vulnerabilities section → Task 1 (`_section_label` skips it) + test. ✓
- Claude dedupe/rank/summarise, boost blogs/cheat-sheets/tooling, top 8–12, British English → Task 4 (`summariser.py` + `prompt.txt`). ✓
- Title `Weekly Cyber Summary — <Mon> to <Sun>`, tag `security/briefing/weekly`, opening line with counts, `###` per story, source links + 1–2 sentence why-it-matters, no title heading, most→least important → Task 5 (`formatter.py`) + test. ✓
- Deliver via Bear with markdown backup → Task 6 reuses `deliver_to_bear`. ✓
- Idempotency across 12:00/13:30 → Task 2 + Task 6 short-circuit. ✓
- Sunday 12:00 + 13:30 fallback, Aqua/Interactive/caffeinate, no pmset → Task 7. ✓
- Short-week handling (summarise what's present, report count) → Task 6 uses actual `n_briefings`; reader returns whatever exists. ✓
- Zero-stories / Claude-failure FAILURE marker + non-zero exit → Task 6 + tests. ✓
- Retention bump 7 → 10 → Task 3. ✓
- `--dry-run` no state changes → Task 6 + test. ✓
- Docs → Task 8. ✓

**Placeholder scan:** No TBD/TODO; all code blocks complete. Two explicit verification notes (the `weekly.py`→`weekly_run.py` rename in Task 6, and the `.env` loader cross-check) are deliberate instructions, not placeholders — each tells the implementer the exact action to take.

**Type consistency:** Story dict keys (`date`, `section`, `headline`, `sources`, `paragraph`, `score`) are identical across reader, summariser, and tests. `SummarisedStory` keys (`headline`, `summary`, `sources`) match across summariser, formatter, and pipeline. `sources` is consistently `list[tuple[str, str]]` (name, url). `run_weekly` / `summarise_week` / `format_weekly` signatures match their call sites in Task 6.

**Naming clash resolved:** entry point is `weekly_run.py` (not `weekly.py`) to coexist with the `weekly/` package — fixed in Task 6 Step 3 and propagated to Tasks 7–8.
