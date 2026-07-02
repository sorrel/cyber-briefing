import os
import threading
import time

import config_loader


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_load_config_reads_base(tmp_path, monkeypatch):
    base = tmp_path / "config.yaml"
    _write(base, "delivery:\n  method: bear\n  slack:\n    channel: C1\n")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", base)
    monkeypatch.setattr(config_loader, "LOCAL_CONFIG_PATH", tmp_path / "config.local.yaml")
    cfg = config_loader.load_config()
    assert cfg["delivery"]["method"] == "bear"


def test_local_overrides_base_deep(tmp_path, monkeypatch):
    # The local file changes only delivery.method; the deep merge must preserve
    # the sibling delivery.slack.channel from the base file.
    base = tmp_path / "config.yaml"
    _write(base, "delivery:\n  method: bear\n  slack:\n    channel: C1\n")
    local = tmp_path / "config.local.yaml"
    _write(local, "delivery:\n  method: slack\n")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", base)
    monkeypatch.setattr(config_loader, "LOCAL_CONFIG_PATH", local)
    cfg = config_loader.load_config()
    assert cfg["delivery"]["method"] == "slack"
    assert cfg["delivery"]["slack"]["channel"] == "C1"


def test_missing_local_file_is_fine(tmp_path, monkeypatch):
    base = tmp_path / "config.yaml"
    _write(base, "delivery:\n  method: bear\n")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", base)
    monkeypatch.setattr(config_loader, "LOCAL_CONFIG_PATH", tmp_path / "absent.yaml")
    cfg = config_loader.load_config()
    assert cfg["delivery"]["method"] == "bear"


# ---------------------------------------------------------------------------
# load_env_with_timeout — bounds the .env load so a writer-less 1Password FIFO
# (locked / unattended) can never hang the process forever, as it did on the
# 2 Jul 2026 morning fire.
# ---------------------------------------------------------------------------

def test_env_load_returns_true_for_regular_file(tmp_path):
    env = tmp_path / ".env"
    _write(env, "CB_TEST_REGULAR=hello\n")
    try:
        assert config_loader.load_env_with_timeout(env, per_attempt_seconds=2) is True
        assert os.environ.get("CB_TEST_REGULAR") == "hello"
    finally:
        os.environ.pop("CB_TEST_REGULAR", None)


def test_env_load_returns_true_for_missing_file(tmp_path):
    # A machine with no .env (or a path that doesn't exist) must complete
    # instantly, exactly like a plain load_dotenv — never hit the timeout.
    assert config_loader.load_env_with_timeout(tmp_path / "absent.env",
                                               per_attempt_seconds=2) is True


def test_env_load_times_out_on_writerless_fifo(tmp_path):
    # The actual bug: a FIFO with no writer blocks open() forever. The helper
    # must give up after the bounded window and report failure, not hang.
    fifo = tmp_path / ".env"
    os.mkfifo(fifo)
    start = time.monotonic()
    result = config_loader.load_env_with_timeout(fifo, per_attempt_seconds=0.3,
                                                 attempts=2)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 3, f"took {elapsed:.1f}s — did not bound the open()"


def test_env_load_reads_fifo_when_writer_attaches(tmp_path):
    # When 1Password (the writer) does attach, the value must stream through.
    fifo = tmp_path / ".env"
    os.mkfifo(fifo)

    def _writer():
        with open(fifo, "w") as f:
            f.write("CB_TEST_FIFO=streamed\n")

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    try:
        assert config_loader.load_env_with_timeout(fifo, per_attempt_seconds=3) is True
        assert os.environ.get("CB_TEST_FIFO") == "streamed"
    finally:
        os.environ.pop("CB_TEST_FIFO", None)
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# arm_runtime_watchdog — a daemon timer that hard-exits if the whole process
# runs longer than max_seconds, so ANY future hang (not just the FIFO) can't
# hold a launchd slot for an hour.
# ---------------------------------------------------------------------------

def test_watchdog_fires_after_timeout():
    fired = threading.Event()
    t = config_loader.arm_runtime_watchdog(max_seconds=0.05, on_timeout=fired.set)
    try:
        assert fired.wait(2) is True
    finally:
        t.cancel()


def test_watchdog_does_not_fire_when_cancelled():
    fired = threading.Event()
    t = config_loader.arm_runtime_watchdog(max_seconds=0.5, on_timeout=fired.set)
    t.cancel()
    assert fired.wait(0.8) is False
