"""Tests for prioritiser/claude_response.py — shared Claude response parsing.

Both scorer.py and deduplicator.py extract a JSON payload from a Claude Messages
response the same way and share the same max_tokens truncation failure mode.
extract_json_text() centralises that; TruncatedResponse makes the truncation
case explicit so each caller can react (scorer retries, deduplicator falls back).
"""

import pytest

from prioritiser.claude_response import TruncatedResponse, extract_json_text


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


def test_extract_returns_plain_json_text():
    assert extract_json_text(_Response('{"items": []}')) == '{"items": []}'


def test_extract_strips_code_fence():
    fenced = '```json\n{"items": [{"id": "a"}]}\n```'
    assert extract_json_text(_Response(fenced)) == '{"items": [{"id": "a"}]}'


def test_extract_joins_multiple_text_blocks():
    resp = _Response("")
    resp.content = [_Block('{"items"'), _Block(": []}")]
    assert extract_json_text(resp) == '{"items": []}'


def test_extract_raises_on_max_tokens_truncation():
    with pytest.raises(TruncatedResponse):
        extract_json_text(_Response('{"items": [', stop_reason="max_tokens"))


def test_truncated_response_is_a_valueerror():
    """scorer._score_chunk catches ValueError — truncation must route there."""
    assert issubclass(TruncatedResponse, ValueError)
