"""Tests for prioritiser/deduplicator.py (global cross-chunk cluster-id reconciliation).

Chunked scoring assigns cluster_ids in independent Claude calls, so the same
story appearing in two chunks gets two mismatched slugs and never collapses.
reconcile_cluster_ids() runs one extra Claude call over all scored items to
assign canonical cluster_ids across the whole set. It is best-effort: any
failure must return the items unchanged rather than break the briefing.
"""

import logging

from prioritiser.clusterer import cluster_items
from prioritiser.deduplicator import _output_budget, reconcile_cluster_ids


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responder):
        self._responder = responder
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responder(kwargs)


class FakeClient:
    """Mimics anthropic.Anthropic just enough for reconcile_cluster_ids.

    `responder` receives the create() kwargs and returns the raw response
    text, or raises to simulate an API error.
    """

    def __init__(self, responder):
        self.messages = FakeMessages(responder)


def _items():
    return [
        {"id": "a", "source": "Wiz Blog", "title": "Wiz uncovers GhostApproval",
         "summary": "trust-boundary flaw", "cluster_id": "wiz-ghostapproval", "composite": 18.6},
        {"id": "b", "source": "The Hacker News", "title": "GhostApproval symlink flaw",
         "summary": "hijack six AI coding assistants", "cluster_id": "thn-symlink", "composite": 17.7},
        {"id": "c", "source": "The Hacker News", "title": "HalluSquatting attack",
         "summary": "weaponises AI hallucinations", "cluster_id": "hallusquat", "composite": 18.1},
    ]


def _responder(text, stop_reason="end_turn"):
    return lambda kwargs: FakeResponse(text, stop_reason)


def test_reconcile_applies_canonical_cluster_ids():
    """Same-story items get an identical slug; distinct stories stay distinct."""
    resp = ('{"items": ['
            '{"id": "a", "cluster_id": "ghostapproval"},'
            '{"id": "b", "cluster_id": "ghostapproval"},'
            '{"id": "c", "cluster_id": "hallusquatting"}]}')
    client = FakeClient(_responder(resp))

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    by_id = {i["id"]: i for i in result}
    assert by_id["a"]["cluster_id"] == "ghostapproval"
    assert by_id["b"]["cluster_id"] == "ghostapproval"
    assert by_id["c"]["cluster_id"] == "hallusquatting"


def test_reconcile_preserves_other_item_fields():
    """Only cluster_id is rewritten; scores and text are untouched."""
    resp = '{"items": [{"id": "a", "cluster_id": "x"}]}'
    client = FakeClient(_responder(resp))

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    a = next(i for i in result if i["id"] == "a")
    assert a["composite"] == 18.6
    assert a["title"] == "Wiz uncovers GhostApproval"


def test_reconcile_absent_item_keeps_original_cluster_id():
    """An item Claude omits from the response retains its pre-reconcile slug."""
    resp = '{"items": [{"id": "a", "cluster_id": "ghostapproval"}]}'
    client = FakeClient(_responder(resp))

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    by_id = {i["id"]: i for i in result}
    assert by_id["a"]["cluster_id"] == "ghostapproval"
    assert by_id["b"]["cluster_id"] == "thn-symlink"  # unchanged
    assert by_id["c"]["cluster_id"] == "hallusquat"   # unchanged


def test_reconcile_malformed_json_returns_items_unchanged():
    """A non-JSON response must not raise — items come back untouched."""
    client = FakeClient(_responder("sorry, I could not do that"))

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    assert [i["cluster_id"] for i in result] == ["wiz-ghostapproval", "thn-symlink", "hallusquat"]


def test_reconcile_api_exception_returns_items_unchanged():
    """An exception from the client must be swallowed — briefing must not break."""
    def boom(kwargs):
        raise RuntimeError("API overloaded")

    client = FakeClient(boom)

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    assert [i["cluster_id"] for i in result] == ["wiz-ghostapproval", "thn-symlink", "hallusquat"]


def test_cross_chunk_duplicate_collapses_after_reconcile():
    """The reported bug: an original blog and its news coverage, scored in
    separate chunks with different slugs, collapse to one story once reconciled
    and clustered."""
    scored = [
        {"id": "a", "source": "Wiz Blog", "title": "Wiz uncovers GhostApproval",
         "summary": "trust-boundary flaw", "cluster_id": "wiz-ghostapproval", "composite": 18.6},
        {"id": "b", "source": "The Hacker News", "title": "GhostApproval symlink flaw",
         "summary": "hijack six AI coding assistants", "cluster_id": "thn-symlink", "composite": 17.7},
    ]
    all_items = [
        {"id": "a", "source": "Wiz Blog", "url": "http://wiz/ghostapproval"},
        {"id": "b", "source": "The Hacker News", "url": "http://thn/ghostapproval"},
    ]
    resp = ('{"items": [{"id": "a", "cluster_id": "ghostapproval"},'
            '{"id": "b", "cluster_id": "ghostapproval"}]}')
    client = FakeClient(_responder(resp))

    reconciled = reconcile_cluster_ids(client, "claude-sonnet-5", scored)
    clustered = cluster_items(reconciled, all_items)

    assert len(clustered) == 1                       # one story, not two
    assert clustered[0]["id"] == "a"                 # higher composite is primary
    assert clustered[0]["also_covered_by"] == [
        {"source": "The Hacker News", "url": "http://thn/ghostapproval"}
    ]


def test_reconcile_single_item_skips_call():
    """With fewer than two items there is nothing to reconcile — no API call."""
    def boom(kwargs):
        raise AssertionError("client.messages.create must not be called")

    client = FakeClient(boom)

    result = reconcile_cluster_ids(client, "claude-sonnet-5", _items()[:1])

    assert result[0]["cluster_id"] == "wiz-ghostapproval"
    assert client.messages.calls == []


def test_output_budget_scales_with_items_and_caps():
    """The reconcile output cap grows per item — a fixed 2000-token cap truncated
    the JSON at ~48 items in production — and is bounded for the non-streaming call."""
    assert _output_budget(60) >= 60 * 50             # comfortably above the old 2000
    assert _output_budget(60) < _output_budget(120)  # scales with item count
    assert _output_budget(10_000) == 16000           # capped for the non-streaming ceiling


def test_reconcile_passes_scaled_budget_to_the_api():
    """reconcile_cluster_ids sizes max_tokens from the item count, not a constant."""
    many = [{"id": f"id{n}"} for n in range(60)]
    client = FakeClient(_responder('{"items": []}'))

    reconcile_cluster_ids(client, "claude-sonnet-5", many)

    assert client.messages.calls[0]["max_tokens"] == _output_budget(60)


def test_reconcile_truncated_output_logged_and_items_unchanged(caplog):
    """A max_tokens-truncated response must be reported clearly (not as a
    cryptic 'Unterminated string') and leave the per-chunk cluster_ids intact."""
    # stop_reason drives the guard, which returns before the body is ever parsed.
    client = FakeClient(_responder('{"items": [', stop_reason="max_tokens"))

    with caplog.at_level(logging.WARNING):
        result = reconcile_cluster_ids(client, "claude-sonnet-5", _items())

    assert [i["cluster_id"] for i in result] == ["wiz-ghostapproval", "thn-symlink", "hallusquat"]
    messages = " ".join(r.getMessage().lower() for r in caplog.records)
    assert "truncat" in messages or "max_tokens" in messages
