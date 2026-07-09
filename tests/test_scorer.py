"""Tests for prioritiser/scorer.py chunk orchestration.

Focused on when the global cluster-id reconciliation pass runs. Scoring itself
is exercised end-to-end elsewhere; here the Anthropic client is faked so no
network call happens, and reconcile_cluster_ids is replaced with a spy.
"""

import prioritiser.scorer as scorer_mod
from prioritiser.scorer import score_items


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


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
