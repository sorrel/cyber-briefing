import json

import anthropic
import pytest

from weekly.summariser import build_payload, parse_response, summarise_week


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


# ---------------------------------------------------------------------------
# Retry behaviour tests
# ---------------------------------------------------------------------------

_ONE_STORY = [
    {"date": "2026-06-19", "section": "Critical", "headline": "F5",
     "sources": [("THN", "https://thn/f5")], "paragraph": "x", "score": 18.1},
]

_VALID_RESPONSE_JSON = json.dumps(
    {"stories": [{"headline": "F5 fix", "summary": "Patch now.", "source_ids": [0]}]}
)


class _FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContentBlock(text)]


class _TransientThenOkMessages:
    """messages.create fails on the first call, succeeds on the second."""

    def __init__(self, error: Exception, ok_text: str) -> None:
        self._error = error
        self._ok_text = ok_text
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            raise self._error
        return _FakeResponse(self._ok_text)


class _AlwaysFailMessages:
    """messages.create always raises."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        raise self._error


class _FakeClient:
    def __init__(self, messages_obj) -> None:
        self.messages = messages_obj


def test_summarise_week_retries_on_transient_api_error(monkeypatch):
    """A single transient APIError should trigger one retry and succeed."""
    fake_messages = _TransientThenOkMessages(
        error=anthropic.APIConnectionError(request=None),
        ok_text=_VALID_RESPONSE_JSON,
    )
    fake_client = _FakeClient(fake_messages)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: fake_client)

    result = summarise_week(_ONE_STORY, {})

    assert fake_messages.call_count == 2
    assert len(result) == 1
    assert result[0]["headline"] == "F5 fix"


def test_summarise_week_raises_runtime_error_on_persistent_failure(monkeypatch):
    """If every attempt fails, RuntimeError must be raised."""
    fake_messages = _AlwaysFailMessages(
        error=anthropic.APIConnectionError(request=None),
    )
    fake_client = _FakeClient(fake_messages)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: fake_client)

    with pytest.raises(RuntimeError, match="Anthropic API call failed after"):
        summarise_week(_ONE_STORY, {})
