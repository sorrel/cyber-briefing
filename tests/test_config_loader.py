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
