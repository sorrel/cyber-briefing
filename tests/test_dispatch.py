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
    cfg = {"method": "slack", "slack": {"channel": "C0EXAMPLE01"}}
    assert deliver(cfg, "T", "b", ["x"]) is True
    assert calls["slack"] == {"channel": "C0EXAMPLE01"}
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
