"""Load config.yaml, overlaid with an optional gitignored config.local.yaml.

Both machines share config.yaml via git. Per-machine differences — e.g. the
work laptop delivering to Slack while the always-on home Mac mini uses Bear —
live in a gitignored ``config.local.yaml`` that is deep-merged over the
committed defaults. A machine with no local file gets the committed config
unchanged (the mini keeps ``delivery.method: bear``).
"""

from pathlib import Path

import yaml

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
