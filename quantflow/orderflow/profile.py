"""Perfil de volumen móvil (POC, VAH, VAL) construido con las barras footprint."""
from __future__ import annotations

from collections import deque

from .footprint import FootprintBar


class VolumeProfile:
    def __init__(self, lookback: int, value_area: float = 0.70):
        self.value_area = value_area
        self._bars: deque[FootprintBar] = deque(maxlen=lookback)
        self._hist: dict[int, float] = {}
        self.bucket: float | None = None

    def add_bar(self, bar: FootprintBar) -> None:
        self.bucket = bar.bucket
        if len(self._bars) == self._bars.maxlen:
            for idx, (b, a) in self._bars[0].levels.items():
                v = self._hist.get(idx, 0.0) - b - a
                if v <= 1e-12:
                    self._hist.pop(idx, None)
                else:
                    self._hist[idx] = v
        self._bars.append(bar)
        for idx, (b, a) in bar.levels.items():
            self._hist[idx] = self._hist.get(idx, 0.0) + b + a

    def levels(self) -> tuple[float | None, float | None, float | None]:
        """(POC, VAH, VAL). El área de valor se expande desde el POC hacia el lado
        con más volumen hasta cubrir `value_area` del total."""
        if not self._hist:
            return None, None, None
        hist = self._hist
        poc = max(hist, key=lambda i: hist[i])
        target = sum(hist.values()) * self.value_area
        mn, mx = min(hist), max(hist)
        lo = hi = poc
        acc = hist[poc]
        while acc < target and (lo > mn or hi < mx):
            up = hist.get(hi + 1, 0.0) if hi < mx else -1.0
            dn = hist.get(lo - 1, 0.0) if lo > mn else -1.0
            if up >= dn:
                hi += 1
                acc += max(up, 0.0)
            else:
                lo -= 1
                acc += max(dn, 0.0)
        b = self.bucket
        if b is None:  # no hay barras: levels() ya devolvió arriba
            return None, None, None
        return round(poc * b + b / 2, 10), round(hi * b + b, 10), round(lo * b, 10)
