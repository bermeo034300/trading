"""VWAP de sesión (reinicio 00:00 UTC) con desviación estándar ponderada por volumen."""
from __future__ import annotations

import math

DAY_MS = 86_400_000


class SessionVWAP:
    def __init__(self):
        self.day: int | None = None
        self.pv = self.v = self.p2v = 0.0

    def add(self, price: float, qty: float, ts: int) -> None:
        day = ts // DAY_MS
        if day != self.day:
            self.day = day
            self.pv = self.v = self.p2v = 0.0
        self.pv += price * qty
        self.p2v += price * price * qty
        self.v += qty

    @property
    def value(self) -> float:
        return self.pv / self.v if self.v > 0 else math.nan

    @property
    def std(self) -> float:
        if self.v <= 0:
            return math.nan
        m = self.pv / self.v
        return math.sqrt(max(self.p2v / self.v - m * m, 0.0))
