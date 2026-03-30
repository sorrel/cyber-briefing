"""Claude API scorer.

Sends a batch of collected items to the Claude API for scoring,
annotation, and clustering according to the system prompt.
"""

import json
import logging
import os
from pathlib import Path

import anthropic

logger = logging.getLogger("cyberbriefing.prioritiser.scorer")

PROMPT_PATH = Path(__file__).parent / "prompt.txt"


def load_prompt() -> str:
    """Load the scoring system prompt from the editable text file."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def score_items(items: list[dict], config: dict | None = None) -> dict:
    """Send items to the Claude API for scoring and annotation.

    Args:
        items: List of standardised item dicts from collectors.
        config: Scoring config from config.yaml (model, weights, etc.)

    Returns:
        Parsed JSON response from Claude with scored/annotated items.
    """
    config = config or {}
    model = config.get("model", "claude-sonnet-4-20250514")
    max_items = config.get("max_items", 15)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score items")
        return {"briefing_date": "", "items": []}

    system_prompt = load_prompt()

    # Prepare the items for the API — strip any fields Claude doesn't need
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

    logger.info("Sending %d items to Claude (%s) for scoring", len(items_for_scoring), model)

    user_message = (
        f"Here are {len(items_for_scoring)} cybersecurity items to score "
        f"for today's briefing. Return the top {max_items} items maximum.\n\n"
        + json.dumps(items_for_scoring, indent=None)
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return {"briefing_date": "", "items": []}

    # Extract text response
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    # Parse JSON response
    try:
        # Strip any accidental code fences
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Claude response as JSON: %s", e)
        logger.debug("Raw response: %s", response_text[:500])
        return {"briefing_date": "", "items": []}

    # Sort by composite score descending
    if "items" in result:
        result["items"].sort(key=lambda x: x.get("composite", 0), reverse=True)
        # Enforce max_items limit
        result["items"] = result["items"][:max_items]

    scored_count = len(result.get("items", []))
    logger.info("Claude returned %d scored items", scored_count)

    return result
