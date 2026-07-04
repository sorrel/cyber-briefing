"""Tests for briefing.py helpers."""

import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from db.state import get_connection, update_scraper_run
from briefing import _run_scraper, _SCRAPER_REGISTRY, _secrets_blocked


@pytest.fixture
def conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    c = get_connection(path)
    yield c
    c.close()
    os.unlink(path)


# ---------------------------------------------------------------------------
# _run_scraper
# ---------------------------------------------------------------------------

class TestRunScraper:
    def _mock_module(self, return_value=None):
        module = MagicMock()
        module.collect.return_value = [{"id": "x", "title": "test"}] if return_value is None else return_value
        return module

    def test_runs_when_due(self, conn):
        module = self._mock_module()
        config = {"my_scraper": {"enabled": True}}
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert len(items) == 1
        module.collect.assert_called_once()

    def test_skips_when_disabled(self, conn):
        module = self._mock_module()
        config = {"my_scraper": {"enabled": False}}
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert items == []
        module.collect.assert_not_called()

    def test_skips_when_not_due(self, conn):
        module = self._mock_module()
        update_scraper_run(conn, "my_scraper")  # just ran
        config = {"my_scraper": {"enabled": True}}
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert items == []
        module.collect.assert_not_called()

    def test_respects_config_interval(self, conn):
        module = self._mock_module()
        # Set last run to 5 hours ago; configured interval is 24h — should NOT run
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO scraper_runs (source, last_checked) VALUES (?, ?)",
            ("my_scraper", old_time),
        )
        conn.commit()
        config = {"my_scraper": {"enabled": True, "check_interval_hours": 24}}
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert items == []

    def test_uses_default_interval_when_not_in_config(self, conn):
        module = self._mock_module()
        # Last ran 25 hours ago; no interval in config → use default of 24h → should run
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO scraper_runs (source, last_checked) VALUES (?, ?)",
            ("my_scraper", old_time),
        )
        conn.commit()
        config = {"my_scraper": {"enabled": True}}  # no check_interval_hours
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert len(items) == 1

    def test_updates_scraper_run_after_collect(self, conn):
        module = self._mock_module()
        config = {"my_scraper": {"enabled": True}}
        _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        # Should now be marked as just-run → won't run again immediately
        items2 = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert items2 == []

    def test_missing_config_key_defaults_to_enabled(self, conn):
        # Scraper not in config dict at all — should still run (default enabled=True)
        module = self._mock_module()
        items = _run_scraper(conn, {}, "absent_scraper", module, default_interval=24)
        assert len(items) == 1

    def test_returns_empty_list_on_no_items(self, conn):
        module = self._mock_module(return_value=[])
        config = {"my_scraper": {"enabled": True}}
        items = _run_scraper(conn, config, "my_scraper", module, default_interval=24)
        assert items == []


# ---------------------------------------------------------------------------
# _SCRAPER_REGISTRY
# ---------------------------------------------------------------------------

class TestScraperRegistry:
    def test_registry_is_non_empty(self):
        assert len(_SCRAPER_REGISTRY) > 0

    def test_each_entry_has_three_elements(self):
        for entry in _SCRAPER_REGISTRY:
            name, module, interval = entry
            assert isinstance(name, str)
            assert hasattr(module, "collect")
            assert isinstance(interval, int) and interval > 0

    def test_no_duplicate_names(self):
        names = [name for name, _, _ in _SCRAPER_REGISTRY]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# _secrets_blocked — fail-fast decision when the .env load timed out (a locked
# 1Password FIFO). A real delivery run must abort early with an accurate
# marker; manual/no-secret modes must still be allowed to proceed.
# ---------------------------------------------------------------------------

class TestSecretsBlocked:
    def test_blocks_real_run_when_env_not_ready(self):
        assert _secrets_blocked(env_ready=False, dry_run=False, gather_only=False) is True

    def test_allows_real_run_when_env_ready(self):
        assert _secrets_blocked(env_ready=True, dry_run=False, gather_only=False) is False

    def test_allows_dry_run_even_when_env_not_ready(self):
        # A manual dry-run is a preview; the scorer reports the missing key to
        # stdout, and dry-runs must not write failure markers or change state.
        assert _secrets_blocked(env_ready=False, dry_run=True, gather_only=False) is False

    def test_allows_gather_only_even_when_env_not_ready(self):
        # Gathering needs no secrets, so a locked machine stays inspectable.
        assert _secrets_blocked(env_ready=False, dry_run=False, gather_only=True) is False
