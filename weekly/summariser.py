"""Send the week's stories to Claude to dedupe, rank, and summarise.

Mirrors the client/parse pattern of prioritiser/scorer.py: model from config,
ANTHROPIC_API_KEY from the environment, a single messages.create call with the
system prompt cached, and ```json fence stripping before json.loads.
"""

import json
import logging
import os
from pathlib import Path

import anthropic

logger = logging.getLogger("cyberbriefing.weekly.summariser")

PROMPT_PATH = Path(__file__).parent / "prompt.txt"
MAX_TOKENS = 8000


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_payload(stories: list[dict]) -> list[dict]:
    """Attach a stable integer id to each story for the Claude call."""
    payload = []
    for i, story in enumerate(stories):
        payload.append({
            "id": i,
            "date": story["date"],
            "section": story["section"],
            "headline": story["headline"],
            "sources": [name for name, _ in story["sources"]],
            "paragraph": story["paragraph"],
            "score": story["score"],
        })
    return payload


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def parse_response(text: str, stories: list[dict]) -> list[dict]:
    """Parse Claude's JSON and map source_ids back to original sources.

    Raises ValueError on JSON parse failure.
    """
    result = json.loads(_strip_fences(text))
    summarised: list[dict] = []
    for entry in result.get("stories", []):
        sources: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for sid in entry.get("source_ids", []):
            if 0 <= sid < len(stories):
                for src in stories[sid]["sources"]:
                    if src not in seen:
                        seen.add(src)
                        sources.append(src)
        summarised.append({
            "headline": entry.get("headline", "").strip(),
            "summary": entry.get("summary", "").strip(),
            "sources": sources,
        })
    return summarised


def summarise_week(stories: list[dict], config: dict | None = None) -> list[dict]:
    """Dedupe, rank, and summarise the week. Raises RuntimeError on failure."""
    config = config or {}
    model = config.get("model", "claude-sonnet-4-6")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot summarise week")

    system_prompt = load_prompt()
    payload = build_payload(stories)
    user_message = (
        f"Here are {len(payload)} cybersecurity stories from this week's "
        f"briefings. Produce the weekly summary.\n\n"
        + json.dumps(payload, indent=None)
    )

    client = anthropic.Anthropic(api_key=api_key)

    last_exc: BaseException | None = None
    response_text: str = ""
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
            )
            response_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            break  # Success — exit the retry loop.
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            last_exc = e
            if attempt < max_attempts:
                logger.warning(
                    "Claude API call failed on attempt %d/%d (%s) — retrying",
                    attempt, max_attempts, e,
                )
            else:
                raise RuntimeError(
                    f"Anthropic API call failed after {max_attempts} attempts: {e}"
                ) from e

    try:
        summarised = parse_response(response_text, stories)
    except ValueError as e:
        logger.debug("Raw response snippet: %s", response_text[:500])
        raise RuntimeError(f"Could not parse Claude response: {e}") from e

    if not summarised:
        raise RuntimeError("Claude returned no stories")
    logger.info("Claude summarised %d stories from %d inputs", len(summarised), len(stories))
    return summarised
