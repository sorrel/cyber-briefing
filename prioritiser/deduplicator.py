"""Global cross-chunk cluster-id reconciliation.

Scoring runs in independent 50-item chunks (see scorer.py). Each chunk is a
separate Claude call that invents its own cluster_id slugs, so the same story
appearing in two chunks (e.g. an original vendor/research blog and the news
coverage of it) gets two mismatched slugs that clusterer.py can never merge.

reconcile_cluster_ids() runs ONE extra Claude call over all surviving scored
items to assign canonical cluster_ids across the whole set. clusterer.py then
collapses them as usual. It is strictly best-effort: any error returns the
items unchanged, so it can never break or empty a briefing.
"""

import json
import logging
from pathlib import Path

from prioritiser.claude_response import TruncatedResponse, extract_json_text

logger = logging.getLogger("cyberbriefing.prioritiser.deduplicator")

PROMPT_PATH = Path(__file__).parent / "dedup_prompt.txt"

# Output is one {"id", "cluster_id"} entry per item, and this call echoes EVERY
# scored item at once (unlike the chunked scorer). 16-hex ids and hyphenated
# slugs are token-dense (~2 chars/token, ~40 tokens/entry), so a fixed 2000-token
# cap truncated the JSON mid-string at ~48 items on busy days — json.loads then
# rejected it as an "unterminated string" and the whole pass silently fell back
# to per-chunk cluster_ids. Size the budget to the item count with headroom,
# capped at a safe non-streaming ceiling.
_OUTPUT_TOKENS_BASE = 512
_OUTPUT_TOKENS_PER_ITEM = 80
_OUTPUT_TOKENS_CAP = 16000


def _output_budget(n_items: int) -> int:
    """max_tokens for the reconcile call, scaled to how many items we echo back."""
    return min(_OUTPUT_TOKENS_CAP, _OUTPUT_TOKENS_BASE + n_items * _OUTPUT_TOKENS_PER_ITEM)


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _apply_cluster_map(scored_items: list[dict], cluster_map: dict[str, str]) -> list[dict]:
    """Return items with cluster_id overwritten from cluster_map (keyed by id).

    Items whose id is absent from cluster_map keep their existing cluster_id.
    """
    result = []
    for item in scored_items:
        canonical = cluster_map.get(item.get("id"))
        result.append({**item, "cluster_id": canonical} if canonical else item)
    return result


def _parse_cluster_map(response_text: str) -> dict[str, str]:
    """Parse Claude's extracted JSON into an {item_id: cluster_id} mapping."""
    data = json.loads(response_text)
    mapping = {}
    for entry in data.get("items", []):
        item_id = entry.get("id")
        cluster_id = entry.get("cluster_id")
        if item_id and cluster_id:
            mapping[item_id] = cluster_id
    return mapping


def reconcile_cluster_ids(client, model: str, scored_items: list[dict]) -> list[dict]:
    """Reassign cluster_ids across all scored items via one Claude call."""
    if len(scored_items) < 2:
        return scored_items

    compact = [
        {
            "id": i.get("id"),
            "source": i.get("source", ""),
            "title": i.get("title", ""),
            "summary": i.get("summary", ""),
        }
        for i in scored_items
    ]
    user_message = (
        f"Here are {len(compact)} scored security stories. Assign canonical "
        f"cluster_ids as instructed.\n\n" + json.dumps(compact, indent=None)
    )

    max_tokens = _output_budget(len(compact))
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": _load_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        cluster_map = _parse_cluster_map(extract_json_text(response))
    except TruncatedResponse:
        # The one failure we can name precisely and act on: the output cap was
        # too small for this many items.
        logger.warning(
            "Cluster reconciliation output truncated at max_tokens=%d for %d "
            "items — keeping per-chunk cluster_ids (raise _OUTPUT_TOKENS_* if "
            "this recurs)",
            max_tokens, len(compact),
        )
        return scored_items
    except Exception as e:
        # Best-effort by design: this is a quality enhancement, never a
        # dependency of a briefing. Any failure (API error, bad JSON, malformed
        # response) must leave the per-chunk cluster_ids intact rather than
        # break or empty the briefing.
        logger.warning(
            "Cluster reconciliation failed (%s) — keeping per-chunk cluster_ids", e
        )
        return scored_items

    if not cluster_map:
        logger.warning(
            "Cluster reconciliation returned no mappings — keeping per-chunk cluster_ids"
        )
        return scored_items

    reconciled = _apply_cluster_map(scored_items, cluster_map)
    logger.info(
        "Cluster reconciliation: %d items → %d distinct cluster_id(s)",
        len(reconciled),
        len({i.get("cluster_id") for i in reconciled}),
    )
    return reconciled
