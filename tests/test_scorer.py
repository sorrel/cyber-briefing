"""Tests for prioritiser/scorer.py chunk orchestration.

Focused on when the global cluster-id reconciliation pass runs, that requests
constrain Claude's output to the scoring schema, and that items whose scoring
call failed are reported so the caller can leave them unseen. Scoring itself is
exercised end-to-end elsewhere; here the Anthropic client is faked so no network
call happens, and reconcile_cluster_ids is replaced with a spy.
"""

import json

import cyberbriefing.prioritiser.scorer as scorer_mod
from cyberbriefing.prioritiser.scorer import score_items


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    def create(self, **kwargs):
        # Every scoring chunk returns one trivially-valid scored item.
        return FakeResponse('{"items": [{"id": "i0", "composite": 15.0, '
                            '"tier": "notable", "cluster_id": "c1"}]}')


class FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = FakeMessages()


def _make_items(n):
    return [
        {"id": f"i{k}", "source": "s", "title": f"t{k}", "url": f"http://x/{k}",
         "snippet": "", "category": "c", "published": ""}
        for k in range(n)
    ]


def _install_fakes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(scorer_mod.anthropic, "Anthropic", FakeAnthropic)
    seen = []
    monkeypatch.setattr(
        scorer_mod, "reconcile_cluster_ids",
        lambda client, model, items: seen.append(len(items)) or items,
    )
    return seen


def test_reconcile_runs_when_scoring_spans_multiple_chunks(monkeypatch):
    """>50 items => 2+ chunks => cross-chunk slugs => reconcile must run."""
    seen = _install_fakes(monkeypatch)

    score_items(_make_items(60), {"model": "m", "max_items": 15})

    assert seen, "reconcile_cluster_ids was not called for a multi-chunk run"


def test_reconcile_skipped_for_single_chunk(monkeypatch):
    """<=50 items => one chunk => slugs already global => no extra call."""
    seen = _install_fakes(monkeypatch)

    score_items(_make_items(10), {"model": "m", "max_items": 15})

    assert seen == [], "reconcile_cluster_ids should not run for a single-chunk run"


def _request_items(kwargs):
    """The item list a faked request was called with."""
    user = kwargs["messages"][0]["content"]
    return json.loads(user[user.index("["):])


class CapturingMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse('{"items": [{"id": "i0", "composite": 15.0, '
                            '"tier": "notable", "cluster_id": "c1"}]}')


def test_scoring_request_constrains_output_to_the_schema(monkeypatch):
    """Claude intermittently drops the top-level closing brace, which discarded
    a whole chunk. output_config.format makes the API enforce valid JSON."""
    seen = _install_fakes(monkeypatch)  # noqa: F841
    captured = CapturingMessages()
    monkeypatch.setattr(
        scorer_mod.anthropic, "Anthropic",
        lambda *a, **k: type("C", (), {"messages": captured})(),
    )

    score_items(_make_items(5), {"model": "m", "max_items": 15})

    assert captured.calls, "no request was made"
    fmt = captured.calls[0].get("output_config", {}).get("format", {})
    assert fmt.get("type") == "json_schema", (
        f"request did not constrain output to a JSON schema: {fmt!r}"
    )
    item_schema = fmt["schema"]["properties"]["items"]["items"]
    # The fields formatter.py and clusterer.py read must be guaranteed present.
    for field in ("id", "composite", "tier", "summary", "annotation",
                  "tags", "cluster_id", "scores"):
        assert field in item_schema["required"], f"{field} not required by schema"


class FirstChunkFailsMessages:
    """Malformed JSON for every request drawn from items i0-i49 (the chunk and
    both of its halves), valid JSON otherwise."""

    POISON = {f"i{k}" for k in range(50)}

    def create(self, **kwargs):
        ids = {item["id"] for item in _request_items(kwargs)}
        if ids <= self.POISON:
            # The real defect: top-level closing brace missing.
            return FakeResponse('{"items": [{"id": "i0", "composite": 15.0, '
                                '"tier": "notable", "cluster_id": "c1"}]')
        return FakeResponse('{"items": [{"id": "i50", "composite": 15.0, '
                            '"tier": "notable", "cluster_id": "c2"}]}')


def test_items_from_a_failed_chunk_are_reported_as_unscored(monkeypatch):
    """A chunk whose call failed must be reported item-by-item, so the caller
    can leave those items unseen for the next fire instead of losing them."""
    _install_fakes(monkeypatch)
    monkeypatch.setattr(
        scorer_mod.anthropic, "Anthropic",
        lambda *a, **k: type("C", (), {"messages": FirstChunkFailsMessages()})(),
    )

    result = score_items(_make_items(60), {"model": "m", "max_items": 15})

    unscored = set(result.get("unscored_ids", []))
    assert unscored == {f"i{k}" for k in range(50)}, (
        f"expected the 50 items of the failed chunk, got {len(unscored)}"
    )
    assert not result["scoring_failed"], "one chunk succeeded — not a total failure"
