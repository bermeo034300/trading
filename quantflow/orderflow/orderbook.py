"""Libro de órdenes L2 (top 20): desequilibrio de liquidez y muros."""
from __future__ import annotations

import statistics

from ..models import BookSnapshot


class OrderBook:
    def __init__(self):
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self.ts = 0

    def update(self, snap: BookSnapshot) -> None:
        self.bids, self.asks, self.ts = snap.bids, snap.asks, snap.ts

    @property
    def ready(self) -> bool:
        return bool(self.bids and self.asks)

    @property
    def mid(self) -> float | None:
        return (self.bids[0][0] + self.asks[0][0]) / 2 if self.ready else None

    @property
    def spread(self) -> float | None:
        return self.asks[0][0] - self.bids[0][0] if self.ready else None

    def imbalance(self, depth: int = 10) -> float | None:
        """(bids - asks) / (bids + asks) en los primeros `depth` niveles. +1 = solo compradores."""
        if not self.ready:
            return None
        b = sum(q for _, q in self.bids[:depth])
        a = sum(q for _, q in self.asks[:depth])
        return (b - a) / (b + a) if b + a > 0 else 0.0

    def walls(self, k: float = 4.0) -> list[dict]:
        """Niveles con tamaño >= k veces la mediana del libro visible."""
        if not self.ready:
            return []
        sizes = [q for _, q in self.bids] + [q for _, q in self.asks]
        med = statistics.median(sizes)
        out = [{"side": "bid", "price": p, "qty": q} for p, q in self.bids if q >= k * med]
        out += [{"side": "ask", "price": p, "qty": q} for p, q in self.asks if q >= k * med]
        return out
