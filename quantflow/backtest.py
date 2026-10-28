"""Backtest: reproduce ticks históricos reales a través del mismo motor que opera en vivo.

Nota: el histórico no incluye el libro de órdenes ni alertas de TradingView, así que
esos dos factores valen 0 (neutrales) durante el backtest.
"""
from __future__ import annotations

import csv
import heapq
import logging
import time
from collections.abc import AsyncIterator

from .config import Config, resolve_path
from .engine import Engine
from .feeds import binance
from .models import Trade
from .storage import Journal

log = logging.getLogger("quantflow.backtest")


async def _rest_source(market: str, symbol: str, hours: float) -> AsyncIterator[Trade]:
    end = int(time.time() * 1000)
    async for t in binance.fetch_agg_trades(market, symbol, end - int(hours * 3_600_000), end):
        yield t


async def _vision_source(market: str, symbol: str, days: int, cache_dir) -> AsyncIterator[Trade]:
    for day in binance.days_back(days):
        path = await binance.download_day(market, symbol, day, cache_dir)
        for t in binance.iter_zip_trades(path, symbol):
            yield t


async def run_backtest(cfg: Config, symbols: list[str], hours: float | None = None, days: int | None = None,
                       out_csv: str | None = None, save_calibration: bool = False) -> dict:
    journal = Journal(":memory:")
    engine = Engine(cfg, journal=journal)
    engine.calibrator.stats = {}  # parte de cero: no mezclar con la calibración en vivo
    market = cfg.exchange.market
    cache_dir = resolve_path(cfg.storage.cache_dir)

    sources = [_vision_source(market, s, days, cache_dir) if days else _rest_source(market, s, hours)
               for s in symbols]
    n = 0
    async for t in _merge(sources):
        engine.on_trade(t)
        n += 1
        if n % 500_000 == 0:
            log.info("%s ticks procesados", f"{n:,}")
    engine.flush()
    for s in symbols:
        log.info("%s: %d barras", s, engine.state(s).bar_count)

    # Cierra lo que quede abierto al último precio para que cuente en las métricas.
    for sym, pos in list(engine.broker.positions.items()):
        st = engine.state(sym)
        engine.broker._close(pos, st.last_price, st.bars[-1].end if st.bars else 0, "fin del backtest")

    trades = sorted(engine.broker.closed, key=lambda t: t["closed_ts"])
    if out_csv and trades:
        path = resolve_path(out_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    if save_calibration:
        live = Journal(resolve_path(cfg.storage.db_path))
        live.save_calibration(engine.calibrator.stats)

    signals = journal.db.execute("SELECT setup, COUNT(*), SUM(accepted) FROM signals GROUP BY setup").fetchall()
    return {
        "stats": engine.broker.stats(),
        "by_setup": _by_setup(trades),
        "signals": {s: {"evaluadas": n, "aceptadas": a} for s, n, a in signals},
        "calibration": engine.calibrator.summary(),
        "trades": trades,
    }


async def _merge(sources: list[AsyncIterator[Trade]]) -> AsyncIterator[Trade]:
    """Combina varias fuentes ordenadas en una sola secuencia cronológica, para que
    el capital, el límite diario y los enfriamientos se apliquen como en vivo."""
    heap: list[tuple[int, int, Trade]] = []
    for i, src in enumerate(sources):
        t = await anext(src, None)
        if t is not None:
            heapq.heappush(heap, (t.ts, i, t))
    while heap:
        _, i, t = heapq.heappop(heap)
        yield t
        nxt = await anext(sources[i], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt.ts, i, nxt))


def _by_setup(trades: list[dict]) -> dict:
    groups: dict[str, list[float]] = {}
    for t in trades:
        groups.setdefault(t["setup"], []).append(t["r_multiple"])
    return {
        k: {"trades": len(v), "win_rate": sum(r > 0 for r in v) / len(v), "expectancy_r": sum(v) / len(v),
            "total_r": sum(v)}
        for k, v in groups.items()
    }
