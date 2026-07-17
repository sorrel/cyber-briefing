"""Claude API scorer.

Sends collected items to the Claude API for scoring, annotation, and clustering
according to the system prompt. Items are sent in chunks to avoid output token
limits; the system prompt is cached across chunks.
"""

import json
import logging
import os
from pathlib import Path

import anthropic

from prioritiser.claude_response import extract_json_text
from prioritiser.deduplicator import reconcile_cluster_ids

logger = logging.getLogger("cyberbriefing.prioritiser.scorer")

PROMPT_PATH = Path(__file__).parent / "prompt.txt"
CHUNK_SIZE = 50
MAX_TOKENS = 8000


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _call_claude(client: anthropic.Anthropic, model: str, system_prompt: str,
                 items: list[dict], max_items: int) -> list[dict]:
    """Send one chunk of items to Claude. Returns a list of scored item dicts.

    Raises ValueError on JSON parse failure — including TruncatedResponse when
    Claude hits max_tokens — so the caller can retry (see _score_chunk).
    """
    user_message = (
        f"Here are {len(items)} cybersecurity items to score "
        f"for today's briefing. Return the top {max_items} items maximum.\n\n"
        + json.dumps(items, indent=None)
    )

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        # Sonnet 5 turns adaptive thinking ON when `thinking` is omitted (it was
        # OFF on Sonnet 4.6). Keep it off: thinking would share the MAX_TOKENS
        # budget with the JSON payload and risk truncating it.
        thinking={"type": "disabled"},
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    # Shared extraction also raises TruncatedResponse (a ValueError) if the model
    # hit max_tokens, so _score_chunk's retry-in-halves catches it and a smaller
    # chunk fits — with a truncation reason in the log, not a bare parse error.
    cleaned = extract_json_text(response)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.debug("Raw response snippet: %s", cleaned[:500])
        raise ValueError(f"JSON parse failed: {e}") from e

    return result.get("items", [])


def _score_chunk(client: anthropic.Anthropic, model: str, system_prompt: str,
                 items: list[dict], max_items: int) -> tuple[list[dict], bool]:
    """Score a chunk, retrying once at half-size if the call fails.

    Returns (items, succeeded). succeeded=False means the API/parsing
    failed for the entire chunk (full call and both halves) — used by the
    caller to detect a total-failure morning and skip mark-seen so the
    next launchd fire can retry the same items.
    """
    try:
        return _call_claude(client, model, system_prompt, items, max_items), True
    except (ValueError, anthropic.APIError) as e:
        logger.warning("Chunk of %d items failed (%s) — retrying in two halves", len(items), e)

    if len(items) <= 1:
        logger.error("Single-item chunk failed — skipping")
        return [], False

    mid = len(items) // 2
    results: list[dict] = []
    any_half_succeeded = False
    for half in (items[:mid], items[mid:]):
        try:
            results.extend(_call_claude(client, model, system_prompt, half, max_items))
            any_half_succeeded = True
        except (ValueError, anthropic.APIError) as e:
            logger.error("Half-chunk of %d items failed — skipping: %s", len(half), e)
    return results, any_half_succeeded


def score_items(items: list[dict], config: dict | None = None) -> dict:
    """Send items to the Claude API for scoring and annotation.

    Args:
        items: List of standardised item dicts from collectors.
        config: Scoring config from config.yaml (model, weights, etc.)

    Returns:
        Parsed JSON response from Claude with scored/annotated items.
    """
    config = config or {}
    model = config.get("model", "claude-sonnet-4-6")
    max_items = config.get("max_items", 15)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score items")
        return {"briefing_date": "", "items": [], "scoring_failed": True,
                "failure_reason": "ANTHROPIC_API_KEY not set"}

    system_prompt = load_prompt()

    items_for_scoring = []
    for item in items:
        scoring_item = {
            "id": item["id"],
            "source": item["source"],
            "title": item["title"],
            "url": item["url"],
            "snippet": item["snippet"],
            "category": item["category"],
            "published": item["published"],
        }
        if "extra" in item:
            scoring_item["extra"] = item["extra"]
        items_for_scoring.append(scoring_item)

    chunks = [
        items_for_scoring[i: i + CHUNK_SIZE]
        for i in range(0, len(items_for_scoring), CHUNK_SIZE)
    ]
    n_chunks = len(chunks)
    logger.info(
        "Sending %d items to Claude (%s) for scoring in %d chunk(s)",
        len(items_for_scoring), model, n_chunks,
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        logger.error("Failed to initialise Anthropic client: %s", e)
        return {"briefing_date": "", "items": [], "scoring_failed": True,
                "failure_reason": f"Anthropic client init failed: {e}"}

    all_scored: list[dict] = []
    chunks_failed = 0
    for i, chunk in enumerate(chunks, 1):
        logger.info("Scoring chunk %d/%d (%d items)", i, n_chunks, len(chunk))
        scored, ok = _score_chunk(client, model, system_prompt, chunk, max_items)
        all_scored.extend(scored)
        if not ok:
            chunks_failed += 1

    # Chunks are scored in independent Claude calls, so the same story split
    # across two chunks gets two mismatched cluster_id slugs that clusterer.py
    # cannot merge. Reconcile them in one extra pass over the scored items.
    # A single chunk already has globally-consistent slugs, so skip the call.
    if n_chunks > 1:
        all_scored = reconcile_cluster_ids(client, model, all_scored)

    all_scored.sort(key=lambda x: x.get("composite", 0), reverse=True)
    high_floor = config.get("high_score_floor", 18)
    high_items = [x for x in all_scored if x.get("composite", 0) >= high_floor]
    other_items = [x for x in all_scored if x.get("composite", 0) < high_floor]
    all_scored = high_items + other_items[:max(0, max_items - len(high_items))]

    logger.info(
        "Claude returned %d scored items across %d chunk(s) (%d chunk(s) failed)",
        len(all_scored), n_chunks, chunks_failed,
    )

    # Total failure = every chunk failed end-to-end. This is the signal that
    # the API was transiently unavailable (e.g. 529 Overloaded across all
    # retries) — distinct from "Claude scored items but none were above the
    # threshold". The caller uses this to skip mark-seen so the next launchd
    # fire can retry the same items rather than seeing only the trickle that
    # arrived in between.
    scoring_failed = n_chunks > 0 and chunks_failed == n_chunks
    return {
        "briefing_date": "",
        "items": all_scored,
        "scoring_failed": scoring_failed,
        "chunks_total": n_chunks,
        "chunks_failed": chunks_failed,
        "failure_reason": (
            f"All {n_chunks} scoring chunk(s) failed — likely Anthropic API overload"
            if scoring_failed else ""
        ),
    }
