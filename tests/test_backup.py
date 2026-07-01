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
