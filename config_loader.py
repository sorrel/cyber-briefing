"""Load config.yaml (overlaid with config.local.yaml) plus startup guards.

Both machines share config.yaml via git. Per-machine differences — e.g. the
work laptop delivering to Slack while the always-on home Mac mini uses Bear —
live in a gitignored ``config.local.yaml`` that is deep-merged over the
committed defaults. A machine with no local file gets the committed config
unchanged (the mini keeps ``delivery.method: bear``).

This module also hosts the two startup-resilience helpers both entry points
(briefing.py, weekly_run.py) call before doing any work: ``load_env_with_timeout``
(bounds the 1Password-FIFO .env load so it can't hang forever) and
``arm_runtime_watchdog`` (a whole-process timeout backstop). See the
2 Jul 2026 hang postmortem in CLAUDE.md.
"""

import logging
import os
import signal
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv

_DIR = Path(__file__).parent
CONFIG_PATH = _DIR / "config.yaml"
LOCAL_CONFIG_PATH = _DIR / "config.local.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Return base with override applied, recursing into nested dicts.

    A scalar (or list) in override replaces the base value; a dict merges key
    by key, so overriding delivery.method leaves delivery.slack.channel intact.
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Load config.yaml, deep-merging config.local.yaml over it when present."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if LOCAL_CONFIG_PATH.exists():
        with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config = _deep_merge(config, local)
    return config


class _EnvLoadTimeout(Exception):
    """Raised internally when a load_dotenv attempt exceeds its time budget."""


def load_env_with_timeout(
    path,
    per_attempt_seconds: float = 30.0,
    attempts: int = 2,
    logger: logging.Logger | None = None,
) -> bool:
    """Load a .env file, bounding each attempt so it can never hang forever.

    Secrets are delivered through a 1Password *local-env FIFO*: opening the
    file blocks until 1Password attaches as a writer, which only happens when
    it is unlocked and the read is authorised. On an unattended or locked fire
    no writer ever comes and ``open()`` blocks indefinitely — with no exception
    to catch (this hung the 2 Jul 2026 morning run for an hour). We arm SIGALRM
    around each ``load_dotenv`` call so the blocked ``open()`` is interrupted
    (EINTR), then either retry — a fresh open re-triggers the 1Password prompt,
    giving a present user another chance to approve — or give up.

    Returns True if a load attempt completed within the window (a missing or
    regular file returns instantly and counts as success), or False if every
    attempt timed out. Never raises on timeout; the caller decides what a False
    means (see ``briefing._secrets_blocked``).

    Must run on the main thread — ``signal`` only arms alarms there.
    """
    log = logger or logging.getLogger("cyberbriefing")

    def _on_alarm(signum, frame):
        raise _EnvLoadTimeout()

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        for attempt in range(1, attempts + 1):
            try:
                signal.setitimer(signal.ITIMER_REAL, per_attempt_seconds)
                load_dotenv(path)
                return True
            except _EnvLoadTimeout:
                log.warning(
                    "Timed out after %.0fs loading secrets from %s "
                    "(attempt %d/%d) — is 1Password unlocked?",
                    per_attempt_seconds, path, attempt, attempts,
                )
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous)

    log.error(
        "Could not load secrets from %s after %d attempt(s) — proceeding "
        "without them.", path, attempts,
    )
    return False


def arm_runtime_watchdog(
    max_seconds: float = 900.0,
    on_timeout=None,
    logger: logging.Logger | None = None,
) -> threading.Timer:
    """Start a daemon timer that hard-exits the process if a run overruns.

    A whole-process backstop against ANY hang — a wedged network call, a stuck
    scraper, a future regression — holding a launchd slot the way the FIFO hang
    did. Deliberately a thread (not SIGALRM) so it never collides with
    ``load_env_with_timeout``'s alarm. ``on_timeout`` defaults to
    ``os._exit(1)`` and is injectable so tests can observe a fire without
    killing the interpreter. Returns the Timer; callers cancel() it on clean
    completion.
    """
    log = logger or logging.getLogger("cyberbriefing")

    def _fire():
        log.error("Runtime watchdog: run exceeded %.0fs — forcing exit.", max_seconds)
        (on_timeout or (lambda: os._exit(1)))()

    timer = threading.Timer(max_seconds, _fire)
    timer.daemon = True
    timer.start()
    return timer
