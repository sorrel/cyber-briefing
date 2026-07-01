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
    posted = []
    monkeypatch.setattr(slack_mod.requests, "post",
                        lambda *a, **k: posted.append(k) or FakeResp(payload={"ok": True, "ts": "1"}))
    assert deliver_to_slack("T", "body", [], {}) is False
    assert posted == []


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


def test_thread_reply_failure_is_non_fatal(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(
        slack_mod, "markdown_to_block_groups",
        lambda t, b: [[{"type": "divider"}], [{"type": "divider"}]],
    )
    seq = [
        FakeResp(payload={"ok": True, "ts": "111.1"}),             # parent posts
        FakeResp(payload={"ok": False, "error": "msg_too_long"}),  # reply fails
    ]
    monkeypatch.setattr(slack_mod.requests, "post", lambda *a, **k: seq.pop(0))
    assert deliver_to_slack("T", "body", [], CFG) is True   # parent OK → True despite reply failure
    assert seq == []                                        # both responses consumed
