from datetime import date
from pathlib import Path

import cyberbriefing.weekly_run as weekly_mod
from cyberbriefing.db import state

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
        weekly_mod, "deliver",
        lambda delivery_cfg, title, body, tags: delivered.update(title=title, body=body, tags=tags) or True,
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


class TestFallbackAfterDeliveryWithSecretsLocked:
    """The weekly mirror of the daily bug: main() checked secrets before
    run_weekly() checked the delivered marker, so a locked 1Password at the
    fallback fire would write FAILURE-weekly over a delivered week."""

    def _main(self, tmp_path, monkeypatch, delivered):
        conn = state.get_connection(str(tmp_path / "s.db"))
        if delivered:
            state.mark_weekly_delivered(conn)
        monkeypatch.setattr(weekly_mod, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(weekly_mod.state, "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(weekly_mod.config_loader, "load_env_with_timeout",
                            lambda *a, **k: False)
        monkeypatch.setattr(weekly_mod.config_loader, "arm_runtime_watchdog",
                            lambda *a, **k: type("W", (), {"cancel": lambda self: None})())
        return weekly_mod.main([])

    def test_delivered_week_exits_cleanly_without_secrets(self, tmp_path, monkeypatch):
        rc = self._main(tmp_path, monkeypatch, delivered=True)

        assert rc == 0
        assert not list(tmp_path.glob("FAILURE-weekly-*.md"))

    def test_undelivered_week_still_fails_without_secrets(self, tmp_path, monkeypatch):
        rc = self._main(tmp_path, monkeypatch, delivered=False)

        assert rc == 1
        assert len(list(tmp_path.glob("FAILURE-weekly-*.md"))) == 1
