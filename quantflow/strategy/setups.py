"""Setups de order flow.

Cada setup recibe el contexto de mercado al cierre de una barra y devuelve
candidatos con entrada, stop, objetivo y un conjunto de factores normalizados
en [-1, 1] orientados a favor del trade (+1 = el factor apoya la operación).

  absorcion          Vendedores (compradores) agresivos golpean el extremo de la
                     vela pero el precio no cede: alguien pasivo grande está
                     absorbiendo. Reversión hacia el valor (POC/VWAP).
  imbalance_apilado  Varios niveles consecutivos con imbalance diagonal: iniciativa
                     agresiva fuerte de un lado. Continuación de tendencia.
  divergencia_cvd    El precio marca nuevo extremo y el delta acumulado no lo
                     confirma: agotamiento. Reversión hacia el valor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TypeGuard

from ..orderflow.footprint import FootprintBar
from ..quant.stats import clip


@dataclass
class MarketContext:
    symbol: str
    bar: FootprintBar
    bars: list[FootprintBar]
    atr: float
    hurst: float
    vwap: float
    vwap_std: float
    poc: float | None
    vah: float | None
    val: float | None
    cvd: list[float]
    book_imbalance: float | None
    tv_bias: int
    stacked_buy: list[tuple[float, float, int]] = field(default_factory=list)
    stacked_sell: list[tuple[float, float, int]] = field(default_factory=list)
    stacked_min: int = 3

    def to_dict(self) -> dict:
        return {
            "atr": self.atr,
            "hurst": self.hurst,
            "regime": "tendencia" if self.hurst > 0.55 else "reversión" if self.hurst < 0.45 else "neutral",
            "vwap": self.vwap,
            "vwap_std": self.vwap_std,
            "poc": self.poc,
            "vah": self.vah,
            "val": self.val,
            "cvd": self.cvd[-1] if self.cvd else 0.0,
            "book_imbalance": self.book_imbalance,
            "tv_bias": self.tv_bias,
            "stacked_buy": self.stacked_buy,
            "stacked_sell": self.stacked_sell,
        }


@dataclass
class Candidate:
    setup: str
    side: int          # +1 largo, -1 corto
    entry: float
    stop: float
    target: float
    features: dict[str, float]
    reasons: list[str]


def _common(ctx: MarketContext, s: int) -> dict[str, float]:
    return {
        "book": clip((ctx.book_imbalance or 0.0) * s),
        "tradingview": float(ctx.tv_bias * s),
    }


def _dispersion(ctx: MarketContext) -> float:
    return ctx.vwap_std if ctx.vwap_std and ctx.vwap_std > 0 else ctx.atr


def _valid(x: float | None) -> TypeGuard[float]:
    return x is not None and not math.isnan(x)


def _reversion_location(ctx: MarketContext, s: int) -> float:
    """Qué tan estirado está el precio en contra del trade (espacio para revertir)."""
    bar = ctx.bar
    if not _valid(ctx.vwap):
        return 0.0
    loc = clip((ctx.vwap - bar.close) * s / (2 * _dispersion(ctx)))
    edge = ctx.val if s > 0 else ctx.vah
    extreme = bar.low if s > 0 else bar.high
    if _valid(edge) and (edge - extreme) * s >= 0:  # tocó o rompió el borde del área de valor
        loc = max(loc, 0.6)
    return loc


def _mean_target(ctx: MarketContext, s: int, entry: float, risk: float, min_rr: float) -> tuple[float, str]:
    """Nivel de valor más cercano (POC, VWAP, borde opuesto del área de valor) que ofrezca min_rr."""
    levels = [("POC", ctx.poc), ("VWAP", ctx.vwap), ("VAH" if s > 0 else "VAL", ctx.vah if s > 0 else ctx.val)]
    opts: list[tuple[str, float]] = []
    for name, level in levels:
        if _valid(level) and (level - entry) * s >= min_rr * risk:
            opts.append((name, level))
    if opts:
        name, target = min(opts, key=lambda o: abs(o[1] - entry))
        return target, f"objetivo en {name} ({target:,.2f})"
    return entry + s * min_rr * risk, f"objetivo a {min_rr:g}R (sin nivel estructural cercano)"


def absorption(ctx: MarketContext, p) -> list[Candidate]:
    bar = ctx.bar
    if bar.volume <= 0 or not ctx.atr > 0:
        return []
    out = []
    dr = bar.delta_ratio
    for s in (1, -1):
        aggression = -dr * s                       # agresión en contra del trade
        held = bar.close_location if s > 0 else 1 - bar.close_location
        if aggression < p.absorption_min_delta or held < 0.5:
            continue
        bid, ask = bar.extreme_volume("low" if s > 0 else "high", 2)
        absorbed = (bid if s > 0 else ask) / bar.volume
        extreme_delta = -bar.min_delta if s > 0 else bar.max_delta
        recovery = ((bar.delta - bar.min_delta) if s > 0 else (bar.max_delta - bar.delta)) / extreme_delta \
            if extreme_delta > 0 else 0.0
        entry = bar.close
        stop = (bar.low if s > 0 else bar.high) - s * p.stop_atr_buffer * ctx.atr
        risk = abs(entry - stop)
        target, treason = _mean_target(ctx, s, entry, risk, p.min_rr)
        features = {
            "orderflow": clip(0.5 * clip(aggression / 0.5, 0, 1) + 0.5 * clip(absorbed / 0.3, 0, 1), 0, 1),
            "delta": clip(recovery * 2 - 1),
            "location": _reversion_location(ctx, s),
            "regime": clip((0.5 - ctx.hurst) * 4),
            **_common(ctx, s),
        }
        reasons = [
            f"absorción: delta {dr:+.0%} del volumen pero cierra en el {held:.0%} del rango a favor",
            f"{absorbed:.0%} del volumen agresivo absorbido en el {'mínimo' if s > 0 else 'máximo'}",
            treason,
        ]
        out.append(Candidate("absorcion", s, entry, stop, target, features, reasons))
    return out


def stacked_imbalance(ctx: MarketContext, p) -> list[Candidate]:
    bar = ctx.bar
    if bar.volume <= 0 or not ctx.atr > 0 or not _valid(ctx.vwap):
        return []
    out = []
    for s in (1, -1):
        zones = ctx.stacked_buy if s > 0 else ctx.stacked_sell
        if not zones or bar.delta_ratio * s <= 0 or (bar.close - ctx.vwap) * s <= 0:
            continue
        longest = max(z[2] for z in zones)
        edge = min(z[0] for z in zones) if s > 0 else max(z[1] for z in zones)
        entry = bar.close
        stop = edge - s * p.stop_atr_buffer * ctx.atr
        if (entry - stop) * s <= 0:
            continue
        risk = abs(entry - stop)
        target = entry + s * p.continuation_rr * risk
        z = (bar.close - ctx.vwap) * s / _dispersion(ctx)
        features = {
            "orderflow": clip(0.4 + 0.2 * (longest - ctx.stacked_min) + 0.2 * (len(zones) - 1), 0, 1),
            "delta": clip(bar.delta_ratio * s * 2),
            "location": clip(1 - abs(z - 1) / 1.5),   # ideal: ~1σ a favor, ni pegado ni sobreextendido
            "regime": clip((ctx.hurst - 0.5) * 4),
            **_common(ctx, s),
        }
        reasons = [
            (
                f"{len(zones)} zona(s) de imbalances "
                f"{'compradores' if s > 0 else 'vendedores'} apilados (máx. {longest} niveles)"
            ),
            f"delta {bar.delta_ratio:+.0%} a favor y precio {'sobre' if s > 0 else 'bajo'} el VWAP ({z:+.1f}σ)",
            f"stop tras la zona de imbalance, objetivo a {p.continuation_rr:g}R",
        ]
        out.append(Candidate("imbalance_apilado", s, entry, stop, target, features, reasons))
    return out


def cvd_divergence(ctx: MarketContext, p) -> list[Candidate]:
    L = p.divergence_lookback
    if len(ctx.bars) < L + 1 or len(ctx.cvd) < L + 1 or not ctx.atr > 0:
        return []
    bar = ctx.bar
    prev = ctx.bars[-L - 1:-1]
    prev_cvd = ctx.cvd[-L - 1:-1]
    now = ctx.cvd[-1]
    scale = sum(abs(b.delta) for b in prev) / len(prev) or 1e-9
    out = []
    for s in (1, -1):
        if s > 0:
            new_extreme = bar.low <= min(b.low for b in prev)
            gap = now - min(prev_cvd)
        else:
            new_extreme = bar.high >= max(b.high for b in prev)
            gap = max(prev_cvd) - now
        if not new_extreme or gap <= 0:
            continue
        entry = bar.close
        stop = (bar.low if s > 0 else bar.high) - s * p.stop_atr_buffer * ctx.atr
        risk = abs(entry - stop)
        target, treason = _mean_target(ctx, s, entry, risk, p.min_rr)
        features = {
            "orderflow": clip(gap / (2 * scale), 0, 1),
            "delta": clip(bar.delta_ratio * s * 2),
            "location": _reversion_location(ctx, s),
            "regime": clip((0.5 - ctx.hurst) * 4),
            **_common(ctx, s),
        }
        reasons = [
            f"divergencia: nuevo {'mínimo' if s > 0 else 'máximo'} de {L} barras sin confirmación del CVD "
            f"({gap / scale:.1f}x el delta medio)",
            treason,
        ]
        out.append(Candidate("divergencia_cvd", s, entry, stop, target, features, reasons))
    return out


SETUPS = {
    "absorcion": absorption,
    "imbalance_apilado": stacked_imbalance,
    "divergencia_cvd": cvd_divergence,
}
