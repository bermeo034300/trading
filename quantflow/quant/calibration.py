"""Calibración bayesiana de probabilidades por setup.

El modelo logístico entrega p_modelo. Cada operación cerrada acumula, por setup,
cuántas se ganaron y la suma de probabilidades predichas. El sesgo observado
(tasa real - predicción media) se encoge hacia 0 con una fuerza previa de
`prior_strength` operaciones, de modo que unas pocas operaciones no distorsionan
el modelo y con muchas la probabilidad converge a la frecuencia real.
"""
from __future__ import annotations

from .stats import clip


class SetupCalibrator:
    def __init__(self, prior_strength: float = 30, state: dict | None = None):
        self.k = prior_strength
        self.stats: dict[str, dict] = state or {}

    def adjust(self, setup: str, p_model: float) -> float:
        s = self.stats.get(setup)
        if not s or s["n"] == 0:
            return p_model
        bias = (s["wins"] - s["sum_pred"]) / (s["n"] + self.k)
        return clip(p_model + bias, 0.01, 0.99)

    def record(self, setup: str, p_model: float, won: bool) -> None:
        s = self.stats.setdefault(setup, {"n": 0, "wins": 0, "sum_pred": 0.0})
        s["n"] += 1
        s["wins"] += int(won)
        s["sum_pred"] += p_model

    def summary(self) -> dict:
        out = {}
        for setup, s in self.stats.items():
            n = s["n"]
            out[setup] = {
                "trades": n,
                "win_rate": s["wins"] / n if n else None,
                "mean_predicted": s["sum_pred"] / n if n else None,
                "bias": (s["wins"] - s["sum_pred"]) / (n + self.k),
            }
        return out
