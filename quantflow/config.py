"""Carga de config.yaml con acceso por atributos (cfg.risk.risk_per_trade_pct)."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config(dict):
    # Directorio del config.yaml que se cargó: las rutas relativas (data/, reports/)
    # se resuelven contra él, no contra el paquete instalado.
    base_dir: Path

    def __getattr__(self, key):
        try:
            value = self[key]
        except KeyError as e:
            raise AttributeError(key) from e
        return Config(value) if isinstance(value, dict) else value


def _find_default_config() -> Path:
    for candidate in (Path.cwd() / "config.yaml", ROOT / "config.yaml"):
        if candidate.is_file():
            return candidate
    return Path.cwd() / "config.yaml"


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path is not None else _find_default_config()
    # utf-8-sig: tolera el BOM que añade el Bloc de notas de Windows
    with open(config_path, encoding="utf-8-sig") as f:
        cfg = Config(yaml.safe_load(f))
    cfg.base_dir = config_path.resolve().parent
    return cfg


def resolve_path(p: str, base: Path | None = None) -> Path:
    """Rutas relativas del config se interpretan desde el directorio del config."""
    path = Path(p)
    return path if path.is_absolute() else (base or ROOT) / path
