"""Shared parsing for Claude Messages API responses.

Both the scorer and the deduplicator ask Claude for a JSON payload and extract
it from the response the same way: join the text blocks and strip an optional
```json ... ``` fence. They also share one failure mode — if the model hits its
max_tokens cap the JSON is cut off mid-string, which json.loads later rejects as
a cryptic "unterminated string".

extract_json_text() centralises that extraction and turns the truncation case
into an explicit, typed TruncatedResponse, so each caller can react in its own
way (the scorer retries a smaller chunk; the deduplicator falls back to
per-chunk cluster_ids) while the log names the real cause.
"""


class TruncatedResponse(ValueError):
    """Claude stopped at max_tokens, so the response body is incomplete JSON.

    Subclasses ValueError so the scorer's existing (ValueError, APIError) retry
    path catches it with no change.
    """


def extract_json_text(response) -> str:
    """Return the JSON text from a Claude Messages response.

    Joins the response's text blocks and strips a leading/trailing ``` fence.
    Raises TruncatedResponse if the model was cut off at max_tokens — the body
    would be half-written JSON, so callers get a clear cause instead of a
    downstream "unterminated string" from json.loads.
    """
    if response.stop_reason == "max_tokens":
        raise TruncatedResponse("Claude response truncated (stop_reason=max_tokens)")

    text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()
