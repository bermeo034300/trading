"""Barras footprint: volumen negociado en cada nivel de precio, separado por agresor.

Cada nivel guarda [bid, ask]:
  bid = volumen de vendedores agresivos (órdenes a mercado que golpearon el bid)
  ask = volumen de compradores agresivos (órdenes a mercado que levantaron el ask)
"""
from __future__ import annotations

import math

from ..models import Trade


def nice_step(x: float) -> float:
    """Redondea x hacia arriba a un paso 'bonito' (1, 2, 2.5, 5 x 10^k)."""
    if x <= 0:
        return 1.0
    exp = math.floor(math.log10(x))
    base = x / 10 ** exp
    for n in (1, 2, 2.5, 5):
        if base <= n + 1e-9:
            return n * 10 ** exp
    return 10 ** (exp + 1)


def auto_bucket(price: float) -> float:
    """Tamaño de nivel por defecto: ~1 punto básico del precio."""
    return nice_step(price * 1e-4)


def _runs(idxs: list[int], n: int) -> list[tuple[int, int]]:
    """Rachas de índices consecutivos de longitud >= n."""
    zones: list[tuple[int, int]] = []
    start: int | None = None
    prev: int | None = None
    for i in idxs:
        if prev is not None and i == prev + 1:
            prev = i
            continue
        if start is not None and prev is not None and prev - start + 1 >= n:
            zones.append((start, prev))
        start = prev = i
    if start is not None and prev is not None and prev - start + 1 >= n:
        zones.append((start, prev))
    return zones


class FootprintBar:
    def __init__(self, symbol: str, start: int, end: int, bucket: float):
        self.symbol = symbol
        self.start = start
        self.end = end
        self.bucket = bucket
        self.levels: dict[int, list[float]] = {}
        self.open = self.high = self.low = self.close = math.nan
        self.volume = 0.0
        self.delta = 0.0
        self.max_delta = 0.0
        self.min_delta = 0.0
        self.trades = 0

    def add(self, t: Trade) -> None:
        idx = math.floor(t.price / self.bucket + 1e-9)
        lv = self.levels.get(idx)
        if lv is None:
            lv = self.levels[idx] = [0.0, 0.0]
        if t.is_buy:
            lv[1] += t.qty
            self.delta += t.qty
        else:
            lv[0] += t.qty
            self.delta -= t.qty
        if self.trades == 0:
            self.open = self.high = self.low = t.price
        else:
            if t.price > self.high:
                self.high = t.price
            if t.price < self.low:
                self.low = t.price
        self.close = t.price
        self.volume += t.qty
        self.trades += 1
        if self.delta > self.max_delta:
            self.max_delta = self.delta
        if self.delta < self.min_delta:
            self.min_delta = self.delta

    def price_of(self, idx: int) -> float:
        return round(idx * self.bucket, 10)

    @property
    def close_location(self) -> float:
        """0 = cierre en el mínimo, 1 = cierre en el máximo."""
        rng = self.high - self.low
        return (self.close - self.low) / rng if rng > 0 else 0.5

    @property
    def delta_ratio(self) -> float:
        return self.delta / self.volume if self.volume > 0 else 0.0

    def poc(self) -> float | None:
        """Point of control: nivel con más volumen (precio medio del nivel)."""
        if not self.levels:
            return None
        idx = max(self.levels, key=lambda i: self.levels[i][0] + self.levels[i][1])
        return self.price_of(idx) + self.bucket / 2

    def extreme_volume(self, side: str, n: int = 2) -> tuple[float, float]:
        """(bid, ask) acumulados en los n niveles más bajos ("low") o más altos ("high")."""
        idxs = sorted(self.levels)
        sel = idxs[:n] if side == "low" else idxs[-n:]
        return sum(self.levels[i][0] for i in sel), sum(self.levels[i][1] for i in sel)

    def imbalances(self, ratio: float, min_level_frac: float = 0.5) -> tuple[list[int], list[int]]:
        """Imbalances diagonales (como los muestran las plataformas de footprint).

        Compra en p: ask(p) >= ratio * bid(p-1)  -> los compradores levantaron ofertas
                     mucho más de lo que los vendedores golpearon justo debajo.
        Venta en p:  bid(p) >= ratio * ask(p+1).
        """
        if not self.levels:
            return [], []
        min_vol = min_level_frac * self.volume / len(self.levels)
        buys, sells = [], []
        for idx, (bid, ask) in self.levels.items():
            below = self.levels.get(idx - 1)
            above = self.levels.get(idx + 1)
            if below is not None and ask > 0 and ask >= min_vol and ask >= ratio * below[0]:
                buys.append(idx)
            if above is not None and bid > 0 and bid >= min_vol and bid >= ratio * above[1]:
                sells.append(idx)
        return sorted(buys), sorted(sells)

    def stacked(self, ratio: float, n: int, min_level_frac: float = 0.5):
        """Zonas de imbalances apilados: (precio_bajo, precio_alto, niveles) para compra y venta."""
        buys, sells = self.imbalances(ratio, min_level_frac)

        def zones(idxs):
            return [(self.price_of(a), self.price_of(b) + self.bucket, b - a + 1) for a, b in _runs(idxs, n)]

        return zones(buys), zones(sells)

    def to_dict(self, ratio: float = 3.0, min_level_frac: float = 0.5, max_levels: int = 60) -> dict:
        buys, sells = self.imbalances(ratio, min_level_frac)
        buy_set, sell_set = set(buys), set(sells)
        poc = self.poc()
        idxs = sorted(self.levels, reverse=True)
        if len(idxs) > max_levels:  # recorta alrededor del cierre
            c = math.floor(self.close / self.bucket)
            idxs = sorted(idxs, key=lambda i: abs(i - c))[:max_levels]
            idxs.sort(reverse=True)
        return {
            "start": self.start,
            "end": self.end,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "delta": self.delta,
            "max_delta": self.max_delta,
            "min_delta": self.min_delta,
            "trades": self.trades,
            "bucket": self.bucket,
            "poc": poc,
            "levels": [
                {
                    "price": self.price_of(i),
                    "bid": self.levels[i][0],
                    "ask": self.levels[i][1],
                    "buy_imb": i in buy_set,
                    "sell_imb": i in sell_set,
                    "poc": poc is not None and abs(self.price_of(i) + self.bucket / 2 - poc) < 1e-9,
                }
                for i in idxs
            ],
        }
