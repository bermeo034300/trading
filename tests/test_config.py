from pathlib import Path

import pytest

from quantflow.config import ROOT, Config, load_config, resolve_path
from quantflow.engine import Engine


def test_load_default_config_has_expected_values():
    cfg = load_config(ROOT / "config.yaml")
    assert isinstance(cfg, Config)
    assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
    assert cfg.bar_seconds == 300
    assert cfg.risk.starting_equity == 10_000
    assert cfg.server.port == 8000
    assert cfg.footprint.bucket["BTCUSDT"] == 10


def test_load_custom_config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("symbols: [SOLUSDT]\nrisk:\n  starting_equity: 500\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.symbols == ["SOLUSDT"]
    assert cfg.risk.starting_equity == 500
    assert cfg.base_dir == tmp_path.resolve()


def test_load_config_prefers_cwd_config(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("symbols: [ADAUSDT]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.symbols == ["ADAUSDT"]
    assert cfg.base_dir == tmp_path.resolve()


def test_missing_attribute_raises_attribute_error():
    cfg = load_config(ROOT / "config.yaml")
    with pytest.raises(AttributeError):
        _ = cfg.no_existe


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/ruta/que/no/existe/config.yaml")


def test_nested_dicts_are_config_instances():
    cfg = load_config(ROOT / "config.yaml")
    assert isinstance(cfg.risk, Config)
    assert isinstance(cfg.exchange, Config)


def test_engine_journal_uses_config_directory(tmp_path):
    (tmp_path / "config.yaml").write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    engine = Engine(cfg)
    db_path = Path(engine.journal.db.execute("PRAGMA database_list").fetchone()[2])
    assert db_path == (tmp_path / "data" / "quantflow.db").resolve()


def test_resolve_path_relative_to_project_root(tmp_path):
    assert resolve_path("data/cache") == ROOT / "data" / "cache"
    assert resolve_path("data/cache", tmp_path) == tmp_path / "data" / "cache"
    absolute = tmp_path / "archivo.db"
    assert resolve_path(absolute) == absolute
    assert resolve_path(absolute, tmp_path) == absolute
