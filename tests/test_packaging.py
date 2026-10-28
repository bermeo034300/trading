"""Guardas de empaquetado: entry point, package data y assets del sdist."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_console_script_and_package_data_declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["quantflow"] == "quantflow.__main__:main"
    assert "dashboard/*.html" in data["tool"]["setuptools"]["package-data"]["quantflow"]


def test_dashboard_asset_ships_with_package():
    assert (ROOT / "quantflow" / "dashboard" / "index.html").is_file()


def test_sdist_includes_runtime_assets():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include config.yaml" in manifest
    assert "recursive-include pine *.pine" in manifest
    assert (ROOT / "pine" / "quantflow_orderflow.pine").is_file()
