"""Carga de config.yaml con acceso por atributos (cfg.risk.risk_per_trade_pct)."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.yaml"


class Config(dict):
    def __getattr__(self, key):
        try:
            value = self[key]
        except KeyError as e:
            raise AttributeError(key) from e
        return Config(value) if isinstance(value, dict) else value


def load_config(path: str | Path | None = None) -> Config:
    # utf-8-sig: tolera el BOM que añade el Bloc de notas de Windows
    with open(path or DEFAULT_PATH, encoding="utf-8-sig") as f:
        return Config(yaml.safe_load(f))


def resolve_path(p: str) -> Path:
    """Rutas relativas del config se interpretan desde la raíz del proyecto."""
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
