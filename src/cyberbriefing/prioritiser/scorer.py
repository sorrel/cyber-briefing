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

from cyberbriefing.prioritiser.claude_response import extract_json_text
from cyberbriefing.prioritiser.deduplicator import reconcile_cluster_ids

logger = logging.getLogger("cyberbriefing.prioritiser.scorer")

PROMPT_PATH = Path(__file__).parent / "prompt.txt"
CHUNK_SIZE = 50
# A 50-item chunk runs to roughly 7k output tokens, which left almost no room
# under the old 8k ceiling. max_tokens is a cap, not a spend, so give it slack.
MAX_TOKENS = 16000

# Claude occasionally ends a scoring response one closing brace short of valid
# JSON — stop_reason "end_turn", nothing truncated, just the top-level "}"
# missing. json.loads rejects the whole payload, and on 3 Aug 2026 that
# discarded 2 of 3 chunks (103 of 123 items). output_config.format makes the
# API constrain the response to this schema instead of asking the prompt
# nicely, so the failure mode cannot recur. Note the JSON Schema subset:
# every object needs "required" + "additionalProperties": false, and numeric
# bounds (the 1-5 score range) are not supported — prompt.txt states those.
_SCORES_SCHEMA = {
    "type": "object",
    "properties": {
        "geographic": {"type": "integer"},
        "domain": {"type": "integer"},
        "actionability": {"type": "integer"},
        "novelty": {"type": "integer"},
    },
    "required": ["geographic", "domain", "actionability", "novelty"],
    "additionalProperties": False,
}
SCORED_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "scores": _SCORES_SCHEMA,
        "composite": {"type": "number"},
        "tier": {
            "type": "string",
            "enum": ["critical", "notable", "radar", "britain", "excluded"],
        },
        "summary": {"type": "string"},
        "annotation": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cluster_id": {"type": "string"},
    },
    "required": [
        "id", "scores", "composite", "tier",
        "summary", "annotation", "tags", "cluster_id",
    ],
    "additionalProperties": False,
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "briefing_date": {"type": "string"},
        "items": {"type": "array", "items": SCORED_ITEM_SCHEMA},
    },
    "required": ["briefing_date", "items"],
    "additionalProperties": False,
}


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
        # Opus 5 / Sonnet 5 turn adaptive thinking ON when `thinking` is omitted
        # (it was OFF on Sonnet 4.6). Keep it off: thinking would share the
        # MAX_TOKENS budget with the JSON payload and risk truncating it.
        # "disabled" is only accepted at effort high or below — the default is
        # high, so don't add an effort override above it here.
        thinking={"type": "disabled"},
        # Constrain the response to the scoring schema — see RESPONSE_SCHEMA.
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
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
        # Log the tail, not the head, and at WARNING: this fails at the very end
        # of the payload, and a DEBUG-only snippet made the 3 Aug 2026 failure
        # impossible to diagnose from the launchd log.
        logger.warning(
            "Unparseable scoring JSON (%d chars), tail: %r", len(cleaned), cleaned[-200:]
        )
        raise ValueError(f"JSON parse failed: {e}") from e

    return result.get("items", [])


def _score_chunk(client: anthropic.Anthropic, model: str, system_prompt: str,
                 items: list[dict], max_items: int
                 ) -> tuple[list[dict], bool, list[dict]]:
    """Score a chunk, retrying once at half-size if the call fails.

    Returns (items, succeeded, unscored). succeeded=False means the API/parsing
    failed for the entire chunk (full call and both halves) — used by the
    caller to detect a total-failure morning and skip mark-seen so the
    next launchd fire can retry the same items.

    unscored lists the input items whose scoring call never came back, whether
    or not the rest of the chunk survived. Without it a partly-failed chunk
    still counted as a success and its items were marked seen, so they never
    came back — 103 items were lost that way on 3 Aug 2026.
    """
    try:
        return _call_claude(client, model, system_prompt, items, max_items), True, []
    except (ValueError, anthropic.APIError) as e:
        logger.warning("Chunk of %d items failed (%s) — retrying in two halves", len(items), e)

    if len(items) <= 1:
        logger.error("Single-item chunk failed — skipping")
        return [], False, list(items)

    mid = len(items) // 2
    results: list[dict] = []
    unscored: list[dict] = []
    any_half_succeeded = False
    for half in (items[:mid], items[mid:]):
        try:
            results.extend(_call_claude(client, model, system_prompt, half, max_items))
            any_half_succeeded = True
        except (ValueError, anthropic.APIError) as e:
            logger.error("Half-chunk of %d items failed — skipping: %s", len(half), e)
            unscored.extend(half)
    return results, any_half_succeeded, unscored


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
    unscored: list[dict] = []
    chunks_failed = 0
    for i, chunk in enumerate(chunks, 1):
        logger.info("Scoring chunk %d/%d (%d items)", i, n_chunks, len(chunk))
        scored, ok, chunk_unscored = _score_chunk(
            client, model, system_prompt, chunk, max_items
        )
        all_scored.extend(scored)
        unscored.extend(chunk_unscored)
        if not ok:
            chunks_failed += 1
    if unscored:
        logger.warning(
            "%d item(s) never reached Claude — left unseen for the next run",
            len(unscored),
        )

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
        # Items whose scoring call failed. The caller must not mark these seen.
        "unscored_ids": [it["id"] for it in unscored if it.get("id")],
        "failure_reason": (
            f"All {n_chunks} scoring chunk(s) failed — likely Anthropic API overload"
            if scoring_failed else ""
        ),
    }
