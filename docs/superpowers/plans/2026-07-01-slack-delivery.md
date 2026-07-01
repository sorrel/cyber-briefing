# Slack Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add Slack as a selectable `delivery.method` (alongside Bear) for both the daily and weekly pipelines, rendering the briefing as a native Slack message with threaded overflow.

**Architecture:** A small dispatcher (`delivery/dispatch.py`) routes the `(title, body, tags)` briefing to the configured channel and *always* writes the markdown backup (the weekly pipeline reads those backups). Slack rendering converts the existing Bear-flavoured markdown into Slack Block Kit via one converter that serves both pipelines. Bear delivery is unchanged except that the backup write moves out of it into the dispatcher.

**Tech Stack:** Python ≥3.12, `requests` (already a dependency), `pyyaml`, `python-dotenv`, `pytest`. Slack Web API `chat.postMessage`. Secrets via the 1Password local env file (standard `load_dotenv`).

## Global Constraints

- Python `requires-python = ">=3.12"`. Run everything with `uv run …`.
- **No new dependencies.** Use `requests` for Slack HTTP (do not add `slack_sdk`).
- Delivery interface everywhere is `(title: str, body: str, tags: list[str]) -> bool`.
- The markdown backup MUST be written for every real delivery except `stdout` — `weekly/reader.py` depends on `~/cyberbriefing-output/`.
- Slack channel ID is `C0BE6PB6S75`, stored in `config.yaml` (`delivery.slack.channel`) — never hardcoded in Python.
- Slack token is `SLACK_BOT_TOKEN` from the environment (`os.environ`); the only bot scope required is `chat:write`.
- Slack `mrkdwn` italic is `_..._` and bold is `*...*` — the inverse of our markdown, which uses `*...*` for italic. The converter must remap this.
- **Commit style (repo house rule):** lowercase Conventional Commits; append a second `-m "claude did his thing on this"`; NEVER add a `Co-Authored-By` trailer.
- Run the full suite with `uv run pytest -q` before each commit; run the named test with `-v` as each step directs.

---

### Task 1: Extract the markdown backup into `delivery/backup.py`

Pure refactor — moves the backup writer out of `bear.py` so every delivery method can share it. Behaviour is unchanged: `deliver_to_bear` still writes a backup (now by delegation).

**Files:**
- Create: `delivery/backup.py`
- Modify: `delivery/bear.py` (remove the backup functions; import and delegate)
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces: `delivery.backup.write_markdown_backup(title: str, body: str, tags: list[str]) -> bool`; `delivery.backup.MARKDOWN_RETENTION_DAYS: int = 10`; `delivery.backup._prune_old_markdown_files(output_dir: Path) -> None`.
- Consumes: nothing from other tasks.

- [x] **Step 1: Write the failing test**

Create `tests/test_backup.py`:

```python
"""Tests for delivery/backup.py."""

import os
import time
from pathlib import Path

from delivery.backup import write_markdown_backup, MARKDOWN_RETENTION_DAYS


def _out_dir(home: Path) -> Path:
    return home / "cyberbriefing-output"


def test_writes_backup_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ok = write_markdown_backup("Cyber Briefing — 2026-07-01", "body text", ["security/briefing/daily"])
    assert ok is True
    files = list(_out_dir(tmp_path).glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "# Cyber Briefing — 2026-07-01" in content
    assert "#security/briefing/daily" in content
    assert "body text" in content


def test_prunes_files_older_than_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = _out_dir(tmp_path)
    out.mkdir(parents=True)
    stale = out / "old.md"
    stale.write_text("old", encoding="utf-8")
    old_mtime = time.time() - (MARKDOWN_RETENTION_DAYS + 1) * 86400
    os.utime(stale, (old_mtime, old_mtime))

    write_markdown_backup("Cyber Briefing — 2026-07-01", "new", ["t"])

    assert not stale.exists()  # pruned
    assert (out / "Cyber Briefing _ 2026-07-01.md").exists()  # em-dash sanitised to _
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'delivery.backup'`.

- [x] **Step 3: Create `delivery/backup.py`**

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backup.py -v`
Expected: PASS (2 passed).

- [x] **Step 5: Slim `delivery/bear.py` to delegate**

In `delivery/bear.py`, change the imports at the top of the file — remove `os` and `Path` (only the backup functions used them) and add the backup import:

Replace:
```python
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

# 10 (not 7) so a slightly-late daily run or a DST shift never prunes
# Monday's backup before the Sunday-midday weekly summary reads the week.
MARKDOWN_RETENTION_DAYS = 10
```
with:
```python
import logging
import subprocess
import sys
import time
from urllib.parse import quote

from delivery.backup import write_markdown_backup
```

In `deliver_to_bear`, replace the three `_write_markdown_file(...)` calls with `write_markdown_backup(...)` (same arguments). The function body becomes:

```python
def deliver_to_bear(title: str, body: str, tags: list[str]) -> bool:
    """Create a Bear note via x-callback-url; always write a markdown backup.

    The markdown backup is the only thing that survives if Bear silently
    drops the URL (e.g. shutting down for an OS update). It is written
    regardless of whether the x-callback-url call appears to succeed.
    """
    if sys.platform != "darwin":
        logger.warning("Not running on macOS — falling back to markdown file")
        return write_markdown_backup(title, body, tags)

    if not _bear_is_running():
        logger.info("Bear not running — launching and waiting for it to settle")
        if not _launch_bear_and_wait():
            logger.warning("Could not bring Bear up; relying on markdown backup")
            return write_markdown_backup(title, body, tags)

    bear_ok = _deliver_via_xcallback(title, body, tags)
    backup_ok = write_markdown_backup(title, body, tags)
    if not bear_ok:
        logger.warning("Bear x-callback-url returned an error — relying on markdown backup")
    return backup_ok
```

Delete the now-duplicated `_write_markdown_file` and `_prune_old_markdown_files` functions from `bear.py` entirely.

- [x] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (the existing suite plus `tests/test_backup.py`).

- [x] **Step 7: Commit**

```bash
git add delivery/backup.py delivery/bear.py tests/test_backup.py
git commit -m "refactor(delivery): extract markdown backup into delivery/backup.py" -m "claude did his thing on this"
```

---

### Task 2: Markdown → Slack blocks converter (`delivery/slack_format.py`)

A pure, well-tested function that turns the briefing markdown into ordered Slack "block groups" (parent message + threaded replies).

**Files:**
- Create: `delivery/slack_format.py`
- Test: `tests/test_slack_format.py`

**Interfaces:**
- Produces:
  - `markdown_to_block_groups(title: str, body: str) -> list[list[dict]]` — `groups[0]` = parent message blocks (starts with a `header` block); `groups[1:]` = threaded-reply block lists, in order. Every group has ≤ `MAX_BLOCKS_PER_MESSAGE` blocks.
  - `_md_inline_to_mrkdwn(text: str) -> str` — inline markdown → Slack mrkdwn.
  - Constants `MAX_BLOCKS_PER_MESSAGE = 45`, `MAX_SECTION_CHARS = 2900`, `MAX_HEADER_CHARS = 150`.
- Consumes: nothing from other tasks.

- [x] **Step 1: Write the failing tests**

Create `tests/test_slack_format.py`:

```python
"""Tests for delivery/slack_format.py."""

from delivery.slack_format import (
    markdown_to_block_groups,
    _md_inline_to_mrkdwn,
    MAX_BLOCKS_PER_MESSAGE,
    MAX_SECTION_CHARS,
    MAX_HEADER_CHARS,
)


def _section_texts(blocks):
    return [b["text"]["text"] for b in blocks if b["type"] == "section"]


def test_link_converted_to_mrkdwn():
    assert _md_inline_to_mrkdwn("[THN](https://thn/f5)") == "<https://thn/f5|THN>"


def test_italic_star_becomes_underscore():
    # Our markdown uses *...* for italic; Slack italic is _..._.
    assert _md_inline_to_mrkdwn("*Score: 18.1*") == "_Score: 18.1_"


def test_bold_double_star_survives_italic_pass():
    # **bold** -> *bold* (Slack bold) and must NOT be turned into _bold_.
    assert _md_inline_to_mrkdwn("**patch now**") == "*patch now*"


def test_url_with_underscores_is_unharmed():
    out = _md_inline_to_mrkdwn("[x](https://e.com/a_b_c)")
    assert out == "<https://e.com/a_b_c|x>"


def test_title_becomes_header_block():
    groups = markdown_to_block_groups("Cyber Briefing — 2026-07-01", "*1 items*\n")
    parent = groups[0]
    assert parent[0]["type"] == "header"
    assert parent[0]["text"]["type"] == "plain_text"
    assert parent[0]["text"]["text"] == "Cyber Briefing — 2026-07-01"


def test_long_title_truncated_to_header_limit():
    groups = markdown_to_block_groups("T" * 300, "body\n")
    assert len(groups[0][0]["text"]["text"]) == MAX_HEADER_CHARS


def test_divider_becomes_divider_block():
    body = "before\n---\nafter\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    assert any(b["type"] == "divider" for b in blocks)


def test_heading_becomes_bold_section_line():
    body = "## 🔴 Critical — act on these\n### F5 RCE\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    joined = "\n".join(_section_texts(blocks))
    assert "*🔴 Critical — act on these*" in joined
    assert "*F5 RCE*" in joined


def test_section_text_never_exceeds_limit():
    body = ("word " * 4000).strip() + "\n"   # one very long paragraph
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    for text in _section_texts(blocks):
        assert len(text) <= MAX_SECTION_CHARS


def test_overflow_splits_into_thread_groups():
    body = "content\n---\n" * 60   # ~60 sections + 60 dividers + header
    groups = markdown_to_block_groups("T", body)
    assert len(groups) > 1                       # overflowed into thread replies
    assert all(len(g) <= MAX_BLOCKS_PER_MESSAGE for g in groups)
    assert groups[0][0]["type"] == "header"      # parent still leads with the title


def test_britain_bullet_converts_link_and_italic():
    body = "- [ICO fines Acme](https://ico/x) · *ICO*\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    joined = "\n".join(_section_texts(blocks))
    assert "<https://ico/x|ICO fines Acme>" in joined
    assert "_ICO_" in joined
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_slack_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'delivery.slack_format'`.

- [x] **Step 3: Create `delivery/slack_format.py`**

```python
"""Convert the briefing's Bear-flavoured markdown into Slack Block Kit.

Both pipelines (daily `format_briefing`, weekly `format_weekly`) emit the same
`(title, body, tags)` markdown shape. This module renders that markdown into a
list of "block groups": the first group is the parent chat.postMessage blocks,
and each subsequent group is posted as a threaded reply so a long briefing never
exceeds Slack's per-message limits.

Correctness trap: our markdown uses `*...*` for *italic* (Score lines, source
captions), but Slack mrkdwn treats `*...*` as *bold* and uses `_..._` for
italic. `_md_inline_to_mrkdwn` remaps this: it protects real bold (`**...**`)
as sentinels, converts single-delimiter emphasis to Slack italic, then restores
the bold as Slack `*...*`.
"""

import re

# Slack limits (kept conservative). A message may hold <=50 blocks; a section
# block's text is capped at 3000 chars; a header block at 150 plain-text chars.
MAX_BLOCKS_PER_MESSAGE = 45
MAX_SECTION_CHARS = 2900
MAX_HEADER_CHARS = 150

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")


def _md_inline_to_mrkdwn(text: str) -> str:
    """Convert inline markdown (links, bold, italic) to Slack mrkdwn."""
    text = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)

    # Protect bold spans as sentinels so the italic pass can't split them.
    bolds: list[str] = []

    def _stash_bold(m: "re.Match") -> str:
        bolds.append(m.group(1) or m.group(2))
        return f"\x00{len(bolds) - 1}\x00"

    text = _BOLD_RE.sub(_stash_bold, text)
    text = _ITALIC_RE.sub(lambda m: f"_{m.group(1) or m.group(2)}_", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"*{bolds[int(m.group(1))]}*", text)
    return text


def _header_block(title: str) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": title[:MAX_HEADER_CHARS], "emoji": True},
    }


def _section_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into <=limit-char pieces on line boundaries.

    A single line longer than `limit` is hard-split so no piece ever exceeds it.
    """
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                pieces.append(current)
            current = line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _chunk(blocks: list[dict], size: int) -> list[list[dict]]:
    if not blocks:
        return [[]]
    return [blocks[i:i + size] for i in range(0, len(blocks), size)]


def markdown_to_block_groups(title: str, body: str) -> list[list[dict]]:
    """Render the briefing markdown into ordered Slack block groups."""
    blocks: list[dict] = [_header_block(title)]
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip("\n")
        buffer.clear()
        if text.strip():
            for piece in _split_text(text, MAX_SECTION_CHARS):
                blocks.append(_section_block(piece))

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "---":
            flush()
            blocks.append({"type": "divider"})
        elif stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            buffer.append(f"*{_md_inline_to_mrkdwn(heading)}*")
        else:
            buffer.append(_md_inline_to_mrkdwn(line))
    flush()

    return _chunk(blocks, MAX_BLOCKS_PER_MESSAGE)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_slack_format.py -v`
Expected: PASS (all tests green).

- [x] **Step 5: Commit**

```bash
git add delivery/slack_format.py tests/test_slack_format.py
git commit -m "feat(delivery): add markdown-to-slack-blocks converter" -m "claude did his thing on this"
```

---

### Task 3: Slack client (`delivery/slack.py`)

Posts the block groups to a channel via `chat.postMessage`, threading overflow under the parent message.

**Files:**
- Create: `delivery/slack.py`
- Test: `tests/test_slack_client.py`

**Interfaces:**
- Consumes: `delivery.slack_format.markdown_to_block_groups`.
- Produces: `delivery.slack.deliver_to_slack(title: str, body: str, tags: list[str], slack_cfg: dict) -> bool` — returns True iff the parent message posted; thread-reply failures are logged, not fatal. Reads `SLACK_BOT_TOKEN` from the environment and `channel` from `slack_cfg`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_slack_client.py`:

```python
"""Tests for delivery/slack.py (Slack Web API client)."""

import delivery.slack as slack_mod
from delivery.slack import deliver_to_slack


class FakeResp:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


CFG = {"channel": "C0BE6PB6S75"}


def test_missing_token_returns_false(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    posted = []
    monkeypatch.setattr(slack_mod.requests, "post", lambda *a, **k: posted.append(k) or FakeResp())
    assert deliver_to_slack("T", "body", [], CFG) is False
    assert posted == []  # never attempted a post


def test_missing_channel_returns_false(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    assert deliver_to_slack("T", "body", [], {}) is False


def test_single_message_posts_parent(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return FakeResp(payload={"ok": True, "ts": "111.1"})

    monkeypatch.setattr(slack_mod.requests, "post", fake_post)
    monkeypatch.setattr(slack_mod, "markdown_to_block_groups", lambda t, b: [[{"type": "divider"}]])

    assert deliver_to_slack("Cyber Briefing", "body", [], CFG) is True
    assert len(calls) == 1
    assert calls[0]["channel"] == "C0BE6PB6S75"
    assert calls[0]["text"] == "Cyber Briefing"
    assert "blocks" in calls[0]
    assert "thread_ts" not in calls[0]


def test_overflow_posts_replies_in_thread(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return FakeResp(payload={"ok": True, "ts": "111.1"})

    monkeypatch.setattr(slack_mod.requests, "post", fake_post)
    monkeypatch.setattr(
        slack_mod, "markdown_to_block_groups",
        lambda t, b: [[{"type": "divider"}], [{"type": "divider"}], [{"type": "divider"}]],
    )

    assert deliver_to_slack("T", "body", [], CFG) is True
    assert len(calls) == 3
    assert "thread_ts" not in calls[0]
    assert calls[1]["thread_ts"] == "111.1"
    assert calls[2]["thread_ts"] == "111.1"


def test_api_error_returns_false(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_mod, "markdown_to_block_groups", lambda t, b: [[{"type": "divider"}]])
    monkeypatch.setattr(
        slack_mod.requests, "post",
        lambda *a, **k: FakeResp(payload={"ok": False, "error": "channel_not_found"}),
    )
    assert deliver_to_slack("T", "body", [], CFG) is False


def test_rate_limit_then_success(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_mod, "markdown_to_block_groups", lambda t, b: [[{"type": "divider"}]])
    monkeypatch.setattr(slack_mod.time, "sleep", lambda s: None)  # no real waiting
    seq = [FakeResp(status_code=429, headers={"Retry-After": "0"}),
           FakeResp(payload={"ok": True, "ts": "1.1"})]
    monkeypatch.setattr(slack_mod.requests, "post", lambda *a, **k: seq.pop(0))
    assert deliver_to_slack("T", "body", [], CFG) is True
    assert seq == []  # both responses consumed → it retried
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_slack_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'delivery.slack'`.

- [x] **Step 3: Create `delivery/slack.py`**

```python
"""Deliver a briefing to a Slack channel via chat.postMessage.

Native message + threaded overflow: the parent message carries the header and
the first blocks; anything beyond Slack's per-message block budget is posted as
threaded replies under the parent. Uses `requests` (already a project dep).
"""

import logging
import os
import time

import requests

from delivery.slack_format import markdown_to_block_groups

logger = logging.getLogger("cyberbriefing.delivery.slack")

_POST_URL = "https://slack.com/api/chat.postMessage"
_TIMEOUT_S = 15
_MAX_RATELIMIT_RETRIES = 3


def deliver_to_slack(title: str, body: str, tags: list[str], slack_cfg: dict) -> bool:
    """Post the briefing to Slack. Returns True iff the parent message posted.

    `tags` are Bear-only metadata and are ignored here. Thread-reply failures
    are logged but not fatal — the parent already carries the headline content
    and the dispatcher writes a markdown backup regardless.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        logger.error("SLACK_BOT_TOKEN not set — cannot deliver to Slack")
        return False
    channel = (slack_cfg or {}).get("channel")
    if not channel:
        logger.error("No delivery.slack.channel configured — cannot deliver to Slack")
        return False

    groups = markdown_to_block_groups(title, body)
    parent_ts = _post_message(token, channel, title, groups[0])
    if parent_ts is None:
        logger.warning("Slack parent message failed to post")
        return False

    replies = groups[1:]
    for i, group in enumerate(replies, start=1):
        if _post_message(token, channel, f"{title} (cont.)", group, thread_ts=parent_ts) is None:
            logger.warning("Slack thread reply %d/%d failed to post", i, len(replies))
    logger.info("Delivered to Slack channel %s (%d message(s))", channel, len(groups))
    return True


def _post_message(token: str, channel: str, fallback_text: str,
                  blocks: list[dict], thread_ts: str | None = None) -> str | None:
    """POST one chat.postMessage. Returns the message ts, or None on failure."""
    payload = {"channel": channel, "text": fallback_text, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    for attempt in range(_MAX_RATELIMIT_RETRIES + 1):
        try:
            resp = requests.post(_POST_URL, headers=headers, json=payload, timeout=_TIMEOUT_S)
        except requests.RequestException as e:
            logger.warning("Slack request error: %s", e)
            return None
        if resp.status_code == 429:
            if attempt < _MAX_RATELIMIT_RETRIES:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                logger.info("Slack rate-limited; retrying after %ds", retry_after)
                time.sleep(retry_after)
                continue
            logger.warning("Slack rate-limited; retries exhausted")
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("Slack returned non-JSON (HTTP %d)", resp.status_code)
            return None
        if data.get("ok"):
            return data.get("ts")
        logger.warning("Slack API error: %s", data.get("error", "unknown"))
        return None
    return None
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_slack_client.py -v`
Expected: PASS (all tests green).

- [x] **Step 5: Commit**

```bash
git add delivery/slack.py tests/test_slack_client.py
git commit -m "feat(delivery): add slack chat.postMessage client with thread overflow" -m "claude did his thing on this"
```

---

### Task 4: Delivery dispatcher (`delivery/dispatch.py`)

Routes `(title, body, tags)` to the configured method and always writes the markdown backup (except `stdout`).

**Files:**
- Create: `delivery/dispatch.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `delivery.backup.write_markdown_backup`, `delivery.bear.deliver_to_bear`, `delivery.bear.deliver_to_stdout`, `delivery.slack.deliver_to_slack`.
- Produces: `delivery.dispatch.deliver(delivery_cfg: dict, title: str, body: str, tags: list[str]) -> bool`. Returns True if the briefing was preserved (backup written); `stdout` returns its own result and writes no backup.

- [x] **Step 1: Write the failing tests**

Create `tests/test_dispatch.py`:

```python
"""Tests for delivery/dispatch.py routing + backup invariant."""

import delivery.dispatch as dispatch_mod
from delivery.dispatch import deliver


def _spy(monkeypatch):
    """Patch every downstream deliverer and return a call-record dict."""
    calls = {"bear": 0, "slack": None, "stdout": 0, "backup": 0}
    monkeypatch.setattr(dispatch_mod, "deliver_to_bear",
                        lambda t, b, tags: calls.__setitem__("bear", calls["bear"] + 1) or True)
    monkeypatch.setattr(dispatch_mod, "deliver_to_slack",
                        lambda t, b, tags, cfg: calls.__setitem__("slack", cfg) or True)
    monkeypatch.setattr(dispatch_mod, "deliver_to_stdout",
                        lambda t, b, tags: calls.__setitem__("stdout", calls["stdout"] + 1) or True)
    monkeypatch.setattr(dispatch_mod, "write_markdown_backup",
                        lambda t, b, tags: calls.__setitem__("backup", calls["backup"] + 1) or True)
    return calls


def test_bear_method_delivers_and_backs_up(monkeypatch):
    calls = _spy(monkeypatch)
    assert deliver({"method": "bear"}, "T", "b", ["x"]) is True
    assert calls["bear"] == 1
    assert calls["backup"] == 1


def test_slack_method_passes_channel_and_backs_up(monkeypatch):
    calls = _spy(monkeypatch)
    cfg = {"method": "slack", "slack": {"channel": "C0BE6PB6S75"}}
    assert deliver(cfg, "T", "b", ["x"]) is True
    assert calls["slack"] == {"channel": "C0BE6PB6S75"}
    assert calls["backup"] == 1


def test_markdown_file_method_only_backs_up(monkeypatch):
    calls = _spy(monkeypatch)
    assert deliver({"method": "markdown_file"}, "T", "b", ["x"]) is True
    assert calls["bear"] == 0
    assert calls["slack"] is None
    assert calls["backup"] == 1


def test_stdout_method_skips_backup(monkeypatch):
    calls = _spy(monkeypatch)
    assert deliver({"method": "stdout"}, "T", "b", ["x"]) is True
    assert calls["stdout"] == 1
    assert calls["backup"] == 0


def test_unknown_method_still_backs_up(monkeypatch):
    calls = _spy(monkeypatch)
    assert deliver({"method": "carrier-pigeon"}, "T", "b", ["x"]) is True
    assert calls["backup"] == 1


def test_slack_failure_still_preserved_via_backup(monkeypatch):
    calls = _spy(monkeypatch)
    monkeypatch.setattr(dispatch_mod, "deliver_to_slack", lambda t, b, tags, cfg: False)
    cfg = {"method": "slack", "slack": {"channel": "C"}}
    assert deliver(cfg, "T", "b", ["x"]) is True   # backup succeeded → preserved
    assert calls["backup"] == 1


def test_none_config_defaults_to_bear(monkeypatch):
    calls = _spy(monkeypatch)
    assert deliver(None, "T", "b", ["x"]) is True
    assert calls["bear"] == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'delivery.dispatch'`.

- [x] **Step 3: Create `delivery/dispatch.py`**

```python
"""Route a formatted briefing to the configured delivery channel.

`delivery.method` selects the channel (bear | slack | stdout | markdown_file).
Regardless of channel, a markdown backup is always written (except for the
ephemeral `stdout` method) because the weekly pipeline reads those backups to
build the Sunday summary. The backup is therefore the durable artifact and the
success criterion: a flaky Slack/Bear post never reports total failure.
"""

import logging

from delivery.backup import write_markdown_backup
from delivery.bear import deliver_to_bear, deliver_to_stdout
from delivery.slack import deliver_to_slack

logger = logging.getLogger("cyberbriefing.delivery.dispatch")


def deliver(delivery_cfg: dict, title: str, body: str, tags: list[str]) -> bool:
    """Deliver via the configured method; always persist a markdown backup.

    Returns True if the briefing was preserved (backup written). `stdout` is
    ephemeral and returns its own result without writing a backup.
    """
    cfg = delivery_cfg or {}
    method = cfg.get("method", "bear")

    if method == "stdout":
        return deliver_to_stdout(title, body, tags)

    if method == "slack":
        if not deliver_to_slack(title, body, tags, cfg.get("slack", {})):
            logger.warning("Slack delivery failed — relying on markdown backup")
    elif method == "bear":
        if not deliver_to_bear(title, body, tags):
            logger.warning("Bear delivery failed — relying on markdown backup")
    elif method == "markdown_file":
        pass  # the backup below IS the delivery
    else:
        logger.error("Unknown delivery method %r — writing markdown backup only", method)

    return write_markdown_backup(title, body, tags)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS (all tests green).

- [x] **Step 5: Commit**

```bash
git add delivery/dispatch.py tests/test_dispatch.py
git commit -m "feat(delivery): add method dispatcher with always-on markdown backup" -m "claude did his thing on this"
```

---

### Task 5: Config + `.env.example`

Expose the Slack option in config and document the token.

**Files:**
- Modify: `config.yaml` (the `delivery:` block)
- Modify: `.env.example`

**Interfaces:**
- Produces: `config["delivery"]["slack"]["channel"]` and the `SLACK_BOT_TOKEN` env var contract consumed by Tasks 3–4/6–7.

- [x] **Step 1: Edit `config.yaml`**

Replace the current `delivery:` block:
```yaml
delivery:
  method: "bear"         # bear | stdout | markdown_file
  bear_tag: "security/briefing/daily"
  markdown_output_dir: "~/cyberbriefing-output"
```
with:
```yaml
delivery:
  method: "bear"         # bear | slack | stdout | markdown_file
  bear_tag: "security/briefing/daily"
  markdown_output_dir: "~/cyberbriefing-output"
  slack:
    channel: "C0BE6PB6S75"   # channel ID; the bot must be invited to this channel
```

- [x] **Step 2: Edit `.env.example`**

Replace the whole file with:
```
# Cyber Briefing Tool — Environment Variables
# Copy this to .env and fill in your values.
#
# Secrets are sourced via the 1Password local env file (beta): mount this .env
# from a 1Password Environment (Desktop app → Environments → Destinations →
# "Local .env file"). 1Password streams the values through the file on read;
# standard dotenv loading works with no code changes and no `op run` wrapper.
# https://www.1password.dev/environments/local-env-file

ANTHROPIC_API_KEY=sk-ant-your-key-here
HACKERONE_API_USER=your_hackerone_username
HACKERONE_API_TOKEN=your_hackerone_api_token
NVD_API_KEY=your_nvd_api_key
GITHUB_TOKEN=your_github_personal_access_token

# Only needed when delivery.method is "slack".
# Slack app bot token; the app needs the chat:write scope and must be invited
# to the channel set in config.yaml (delivery.slack.channel).
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
```

- [x] **Step 3: Sanity-check config parses**

Run: `uv run python -c "import yaml; print(yaml.safe_load(open('config.yaml'))['delivery']['slack']['channel'])"`
Expected: prints `C0BE6PB6S75`.

- [x] **Step 4: Commit**

```bash
git add config.yaml .env.example
git commit -m "feat(config): add slack delivery channel and SLACK_BOT_TOKEN" -m "claude did his thing on this"
```

---

### Task 6: Wire the daily pipeline to the dispatcher + slim `bear.py`

The daily pipeline now delivers via `deliver(...)`; Bear stops writing its own backup (the dispatcher owns it). The scoring-failure escalation routes through the configured method too.

**Files:**
- Modify: `briefing.py` (imports; Stage 3 delivery; `_deliver_scoring_failure_to_bear`)
- Modify: `delivery/bear.py` (remove backup from `deliver_to_bear`)

**Interfaces:**
- Consumes: `delivery.dispatch.deliver`, `delivery.bear.deliver_to_stdout`.
- Produces: renamed `briefing._deliver_scoring_failure(delivery_cfg: dict, reason: str, new_item_count: int) -> bool`.

- [x] **Step 1: Slim `deliver_to_bear` in `delivery/bear.py`**

Remove the `from delivery.backup import write_markdown_backup` import added in Task 1 (Bear no longer writes the backup — the dispatcher does). Then replace `deliver_to_bear` with:

```python
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
```

- [x] **Step 2: Rewire delivery in `briefing.py`**

Change the delivery import line (currently `from delivery.bear import deliver_to_bear, deliver_to_stdout`) to:
```python
from delivery.bear import deliver_to_stdout
from delivery.dispatch import deliver
```

Replace the Stage 3 delivery block (the `if dry_run: … else: delivery_method = …` cascade) with:
```python
    if dry_run:
        success = deliver_to_stdout(title, body, tags)
    else:
        success = deliver(config.get("delivery", {}), title, body, tags)
```

- [x] **Step 3: Route the scoring-failure escalation through the dispatcher**

Rename `_deliver_scoring_failure_to_bear` to `_deliver_scoring_failure` and give it the delivery config; replace its final `deliver_to_bear(...)` call:

```python
def _deliver_scoring_failure(delivery_cfg: dict, reason: str, new_item_count: int) -> bool:
    """Send a short error note via the configured method when the *last*
    scheduled fire of the day still couldn't score. Gives the user visibility
    instead of silence. Returns whether the note (or its markdown backup) was
    preserved.
    """
    logger = logging.getLogger("cyberbriefing")
    date = datetime.now().strftime("%Y-%m-%d")
    title = f"Cyber Briefing — {date} — SCORING FAILED"
    body = (
        f"# Cyber Briefing — {date}\n\n"
        f"> **Scoring failed today.** {new_item_count} new items were gathered "
        f"but Claude scoring could not complete.\n\n"
        f"**Reason:** {reason or 'unknown'}\n\n"
        "The full markdown backup of today's gathered items is not produced "
        "in this state — only scored items are formatted. Check "
        "`/tmp/cyberbriefing.log` for per-chunk API errors, and the "
        "`FAILURE-{date}.md` file in `~/cyberbriefing-output/` for diagnosis hints.\n\n"
        "*This note was delivered because the 07:30 fallback fire also failed — "
        "no further automatic retry will run today.*\n"
    ).replace("{date}", date)
    try:
        return deliver(delivery_cfg, title, body,
                       ["security/briefing/daily", "security/briefing/failure"])
    except Exception as e:
        logger.error("Failed to deliver scoring-failure note: %s", e)
        return False
```

Update its call site inside `run_pipeline` (in the `if scoring_failed:` branch) from
`_deliver_scoring_failure_to_bear(reason, len(new_items))` to:
```python
                _deliver_scoring_failure(config.get("delivery", {}), reason, len(new_items))
```

- [x] **Step 4: Verify the daily pipeline still runs and imports cleanly**

Run: `uv run python briefing.py --dry-run`
Expected: runs the pipeline and prints a briefing (or "No new items…") to stdout — dry-run uses `deliver_to_stdout`, so no Bear/Slack call and no state changes.

- [x] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (`test_briefing.py` imports `briefing` — confirms the rewire imports cleanly).

- [x] **Step 6: Commit**

```bash
git add briefing.py delivery/bear.py
git commit -m "feat(delivery): route daily pipeline through the delivery dispatcher" -m "claude did his thing on this"
```

---

### Task 7: Wire the weekly pipeline to the dispatcher

The Sunday weekly summary honours `delivery.method` too.

**Files:**
- Modify: `weekly_run.py` (imports; load delivery config; `run_weekly` signature + delivery call)
- Modify: `tests/test_weekly_pipeline.py` (patch `weekly_mod.deliver` instead of `deliver_to_bear`)

**Interfaces:**
- Consumes: `delivery.dispatch.deliver`, `delivery.bear.deliver_to_stdout`.
- Produces: `weekly_run.run_weekly(output_dir, run_date, dry_run, config, conn, delivery_cfg=None) -> int`; `weekly_run._load_delivery_config() -> dict`.

- [x] **Step 1: Update the failing test first**

In `tests/test_weekly_pipeline.py`, in `test_run_weekly_real_marks_delivered`, replace the `deliver_to_bear` monkeypatch with a `deliver` monkeypatch (note the dispatcher's leading `delivery_cfg` arg):

```python
    monkeypatch.setattr(
        weekly_mod, "deliver",
        lambda delivery_cfg, title, body, tags: delivered.update(title=title, body=body, tags=tags) or True,
    )
```

- [x] **Step 2: Run the weekly tests to verify the real-delivery test now fails**

Run: `uv run pytest tests/test_weekly_pipeline.py -v`
Expected: FAIL — `test_run_weekly_real_marks_delivered` errors with `AttributeError: <module 'weekly_run'> does not have the attribute 'deliver'` (the module still imports `deliver_to_bear`, not `deliver`). The other three weekly tests still pass.

- [x] **Step 3: Rewire `weekly_run.py`**

Change the delivery import (currently `from delivery.bear import deliver_to_bear, deliver_to_stdout`) to:
```python
from delivery.bear import deliver_to_stdout
from delivery.dispatch import deliver
```

Add a delivery-config loader next to `_load_scoring_config`:
```python
def _load_delivery_config() -> dict:
    """Load the delivery config block (method + slack channel)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f).get("delivery", {})
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not load delivery config: %s", e)
        return {}
```

Change the `run_weekly` signature to accept the delivery config:
```python
def run_weekly(output_dir: Path, run_date: date, dry_run: bool,
               config: dict, conn, delivery_cfg: dict | None = None) -> int:
```

Replace the real-delivery line `deliver_to_bear(title, body, tags)` with:
```python
    deliver(delivery_cfg or {}, title, body, tags)
```

In `main`, load the delivery config and pass it through:
```python
    config = _load_scoring_config()
    delivery_cfg = _load_delivery_config()
    conn = state.get_connection()
    return run_weekly(OUTPUT_DIR, date.today(), args.dry_run, config, conn, delivery_cfg)
```

- [x] **Step 4: Run the weekly tests to verify they pass**

Run: `uv run pytest tests/test_weekly_pipeline.py -v`
Expected: PASS (all four). The real-delivery test's patched `weekly_mod.deliver` is now what `run_weekly` calls.

- [x] **Step 5: Verify the weekly pipeline imports and runs in dry-run**

Run: `uv run python weekly_run.py --dry-run`
Expected: prints a weekly summary to stdout (or writes a `FAILURE-weekly-…` marker if there are no backups for the week) — either way, no exception and no Bear/Slack call.

- [x] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add weekly_run.py tests/test_weekly_pipeline.py
git commit -m "feat(delivery): route weekly pipeline through the delivery dispatcher" -m "claude did his thing on this"
```

---

### Task 8: Documentation — README + CLAUDE.md

Document the Slack option, the new modules, and the secrets mechanism.

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (docs only).

- [x] **Step 1: Update `README.md`**

1. The Configuration bullet — replace `Switch delivery method (bear, stdout, or markdown_file)` with:
   ```
   - Switch delivery method (bear, slack, stdout, or markdown_file)
   ```

2. In the "API keys needed" table, add a row after the `GITHUB_TOKEN` row:
   ```
   | `SLACK_BOT_TOKEN` | Slack app (chat:write) | Optional (only for `delivery.method: slack`) |
   ```

3. Add a new section immediately before `## CLI options`:
   ```markdown
   ## Slack delivery

   Set `delivery.method: slack` in `config.yaml` to post the briefing to a Slack
   channel instead of Bear. Both the daily briefing and the weekly summary honour
   this setting; a dated markdown backup is still written to
   `~/cyberbriefing-output/` regardless.

   One-time setup:

   1. Create a Slack app, add the **`chat:write`** bot scope, and install it to
      your workspace.
   2. `/invite` the bot into the target channel.
   3. Put the channel ID in `config.yaml` under `delivery.slack.channel`.
   4. Provide `SLACK_BOT_TOKEN` via your `.env` (see below).

   The briefing is rendered as a native Slack message; anything longer than
   Slack's per-message limit is posted as threaded replies under it.
   ```

- [x] **Step 2: Update `CLAUDE.md`**

1. In "## What this is", change `delivered to Bear Notes` to `delivered to Bear Notes or a Slack channel (configurable)`.

2. In the Architecture tree, replace the `delivery/` block with:
   ```
   delivery/
     formatter.py       ← Converts scored items → markdown (title, body, tags)
     dispatch.py        ← Routes (title, body, tags) to the configured delivery.method; always writes the markdown backup
     bear.py            ← Bear Notes via x-callback-url (Bear-only; backup lives in dispatch/backup now)
     slack.py           ← Slack chat.postMessage delivery (native message + threaded overflow)
     slack_format.py    ← Converts briefing markdown → Slack Block Kit groups
     backup.py          ← Always-on markdown backup to ~/cyberbriefing-output/ (read by the weekly pipeline)
   ```

3. In "## Pipeline flow", change step 5 from `**Deliver**: Bear Notes (real run) or stdout (--dry-run)` to:
   ```
   5. **Deliver**: via `delivery.method` — Bear Notes or Slack (real run) or stdout (--dry-run); a markdown backup is always written
   ```

4. In "## Key tuning levers", add a row to the table:
   ```
   | Delivery target (bear / slack / stdout / markdown_file) | `config.yaml` → `delivery.method` (+ `delivery.slack.channel` for Slack) |
   ```

5. Add a new section immediately before "## Bear delivery bug — investigation and fix (30 April 2026)":
   ```markdown
   ## Slack delivery

   Set `config.yaml` → `delivery.method: slack` to deliver to a Slack channel
   instead of Bear. Applies to both the daily (`briefing.py`) and weekly
   (`weekly_run.py`) pipelines, which both route through `delivery/dispatch.py`.

   - **Auth:** `SLACK_BOT_TOKEN` (env, via the 1Password local env file). Only
     the `chat:write` bot scope is needed; the bot must be invited to the channel.
   - **Channel:** `delivery.slack.channel` in `config.yaml` (a channel ID; never
     hardcoded in Python).
   - **Rendering:** `delivery/slack_format.py` converts the briefing markdown to
     Slack Block Kit — note Slack's `*bold*` / `_italic_` is the inverse of our
     markdown's `*italic*`, which the converter remaps. Long briefings overflow
     into threaded replies under the parent message.
   - **Backup invariant:** `delivery/dispatch.py` always writes the
     `~/cyberbriefing-output/` markdown backup for every method except `stdout`,
     because `weekly/reader.py` reads those backups. Bear/Slack posting is
     best-effort; the backup is the durable artifact and the success signal.
   - **Secrets caveat:** the 1Password local env file prompts for authorization
     on first read after 1Password *locks*, and its FIFO does not support
     concurrent readers. For the unattended launchd fires to obtain the token,
     1Password must stay unlocked. The daily and weekly fire windows do not
     overlap, so the single-reader limit is not a concern.
   ```

6. In "## Secrets", add to the required/optional keys list:
   ```
   - `SLACK_BOT_TOKEN` — optional, only for `delivery.method: slack` (Slack app bot token, `chat:write` scope)
   ```
   and change the opening line `Uses \`.env\` file (gitignored).` to note the source:
   ```
   Uses a `.env` file (gitignored), sourced via the 1Password local env file
   (values streamed on read; standard `load_dotenv` — no `op run`). Required keys:
   ```

- [x] **Step 3: Verify docs render (no broken tables/fences)**

Run: `uv run python -c "import pathlib; [print(p, 'ok') for p in ['README.md','CLAUDE.md'] if pathlib.Path(p).read_text()]"`
Expected: prints `README.md ok` and `CLAUDE.md ok` (sanity that both files are readable; visually skim the diff for fence/table alignment).

- [x] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document slack delivery option and delivery dispatcher" -m "claude did his thing on this"
```

---

## Final verification

- [x] Run the whole suite: `uv run pytest -q` — 106 passed (1 Jul 2026).
- [x] Daily dry-run: `uv run python briefing.py --dry-run` — green; full tiered briefing to stdout, only the expected optional-token (HackerOne/GitHub) skips, no state changes.
- [x] Weekly dry-run: `uv run python weekly_run.py --dry-run` — empty-week path confirmed: `~/cyberbriefing-output/` had 0 daily backups, so it read 0 stories, wrote `FAILURE-weekly-<date>.md`, exited non-zero, no exception. Happy path (summarise → format → dispatch) therefore covered only by unit tests, not this run.
- [ ] (Optional, needs a real token + invited bot) Temporarily set `delivery.method: slack`, export `SLACK_BOT_TOKEN`, run `uv run python briefing.py` and confirm the message lands in `C0BE6PB6S75` and a backup appears in `~/cyberbriefing-output/`.
