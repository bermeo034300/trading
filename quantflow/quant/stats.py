"""Estadística de mercado: volatilidad, régimen y utilidades numéricas."""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def atr(bars: Sequence, period: int = 14) -> float:
    """Average True Range simple de las últimas `period` barras."""
    if not bars:
        return math.nan
    recent = list(bars)[-(period + 1):]
    if len(recent) < 2:
        return recent[-1].high - recent[-1].low
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(recent, recent[1:])
    ]
    return float(np.mean(trs))


def hurst(prices: Sequence[float], max_lag: int = 20) -> float:
    """Exponente de Hurst por escalado de la varianza de los incrementos.

    H > 0.5: el precio persiste (tendencia). H < 0.5: revierte a la media.
    Devuelve 0.5 (neutral) si no hay datos suficientes.
    """
    x = np.log(np.asarray(prices, dtype=float))
    if len(x) < max_lag * 2:
        return 0.5
    lags = np.arange(2, max_lag)
    tau = np.array([np.std(x[lag:] - x[:-lag]) for lag in lags])
    mask = tau > 0
    if mask.sum() < 3:
        return 0.5
    slope = np.polyfit(np.log(lags[mask]), np.log(tau[mask]), 1)[0]
    return float(np.clip(slope, 0.0, 1.0))
