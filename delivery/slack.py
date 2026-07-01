"""Deliver a briefing to a Slack channel via chat.postMessage.

Native message + threaded overflow: the parent message carries the header and
the first blocks; anything beyond Slack's per-message block budget is posted as
threaded replies under the parent. Uses `requests` (already a project dep).
"""

import logging
import os
import time

import requests

from delivery.slack_format import markdown_to_block_groups

logger = logging.getLogger("cyberbriefing.delivery.slack")

_POST_URL = "https://slack.com/api/chat.postMessage"
_TIMEOUT_S = 15
_MAX_RATELIMIT_RETRIES = 3


def deliver_to_slack(title: str, body: str, tags: list[str], slack_cfg: dict) -> bool:
    """Post the briefing to Slack. Returns True iff the parent message posted.

    `tags` are Bear-only metadata and are ignored here. Thread-reply failures
    are logged but not fatal — the parent already carries the headline content
    and the dispatcher writes a markdown backup regardless.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        logger.error("SLACK_BOT_TOKEN not set — cannot deliver to Slack")
        return False
    channel = (slack_cfg or {}).get("channel")
    if not channel:
        logger.error("No delivery.slack.channel configured — cannot deliver to Slack")
        return False

    groups = markdown_to_block_groups(title, body)
    parent_ts = _post_message(token, channel, title, groups[0])
    if parent_ts is None:
        logger.warning("Slack parent message failed to post")
        return False

    replies = groups[1:]
    for i, group in enumerate(replies, start=1):
        if _post_message(token, channel, f"{title} (cont.)", group, thread_ts=parent_ts) is None:
            logger.warning("Slack thread reply %d/%d failed to post", i, len(replies))
    logger.info("Delivered to Slack channel %s (%d message(s))", channel, len(groups))
    return True


def _post_message(token: str, channel: str, fallback_text: str,
                  blocks: list[dict], thread_ts: str | None = None) -> str | None:
    """POST one chat.postMessage. Returns the message ts, or None on failure."""
    payload = {"channel": channel, "text": fallback_text, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    for attempt in range(_MAX_RATELIMIT_RETRIES + 1):
        try:
            resp = requests.post(_POST_URL, headers=headers, json=payload, timeout=_TIMEOUT_S)
        except requests.RequestException as e:
            logger.warning("Slack request error: %s", e)
            return None
        if resp.status_code == 429:
            if attempt < _MAX_RATELIMIT_RETRIES:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                logger.info("Slack rate-limited; retrying after %ds", retry_after)
                time.sleep(retry_after)
                continue
            logger.warning("Slack rate-limited; retries exhausted")
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("Slack returned non-JSON (HTTP %d)", resp.status_code)
            return None
        if data.get("ok"):
            return data.get("ts")
        logger.warning("Slack API error: %s", data.get("error", "unknown"))
        return None
    return None
