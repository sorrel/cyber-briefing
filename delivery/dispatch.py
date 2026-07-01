"""Route a formatted briefing to the configured delivery channel.

`delivery.method` selects the channel (bear | slack | stdout | markdown_file).
Regardless of channel, a markdown backup is always written (except for the
ephemeral `stdout` method) because the weekly pipeline reads those backups to
build the Sunday summary. The backup is therefore the durable artifact and the
success criterion: a flaky Slack/Bear post never reports total failure.
"""

import logging

from delivery.backup import write_markdown_backup
from delivery.bear import deliver_to_bear, deliver_to_stdout
from delivery.slack import deliver_to_slack

logger = logging.getLogger("cyberbriefing.delivery.dispatch")


def deliver(delivery_cfg: dict, title: str, body: str, tags: list[str]) -> bool:
    """Deliver via the configured method; always persist a markdown backup.

    Returns True if the briefing was preserved (backup written). `stdout` is
    ephemeral and returns its own result without writing a backup.
    """
    cfg = delivery_cfg or {}
    method = cfg.get("method", "bear")

    if method == "stdout":
        return deliver_to_stdout(title, body, tags)

    if method == "slack":
        if not deliver_to_slack(title, body, tags, cfg.get("slack", {})):
            logger.warning("Slack delivery failed — relying on markdown backup")
    elif method == "bear":
        if not deliver_to_bear(title, body, tags):
            logger.warning("Bear delivery failed — relying on markdown backup")
    elif method == "markdown_file":
        pass  # the backup below IS the delivery
    else:
        logger.error("Unknown delivery method %r — writing markdown backup only", method)

    return write_markdown_backup(title, body, tags)
