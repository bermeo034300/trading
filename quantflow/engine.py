"""Motor principal: convierte ticks en barras footprint y, al cierre de cada barra,
evalúa los setups, decide y gestiona las operaciones simuladas.

El mismo motor sirve para tiempo real y para backtest (se le pasan los ticks en orden).
"""
from __future__ import annotations

import math
import time
from collections import deque

from .config import Config, resolve_path
from .execution.paper import PaperBroker
from .models import BookSnapshot, Signal, Trade, TradingViewAlert
from .orderflow import FootprintBar, OrderBook, SessionVWAP, VolumeProfile, auto_bucket
from .quant.calibration import SetupCalibrator
from .quant.stats import atr, hurst
from .storage import Journal
from .strategy.decision import DecisionEngine
from .strategy.setups import MarketContext


def normalize_symbol(sym: str) -> str:
    """'BINANCE:BTCUSDT.P' -> 'BTCUSDT'."""
    sym = sym.upper().split(":")[-1]
    for suffix in (".P", "PERP"):
        if sym.endswith(suffix):
            sym = sym[: -len(suffix)]
    return sym


class SymbolState:
    def __init__(self, symbol: str, cfg: Config):
        self.symbol = symbol
        self.bucket: float | None = cfg.footprint.get("bucket", {}).get(symbol)
        self.bar: FootprintBar | None = None
        self.bars: deque[FootprintBar] = deque(maxlen=max(500, cfg.profile.lookback_bars))
        self.profile = VolumeProfile(cfg.profile.lookback_bars, cfg.profile.value_area)
        self.vwap = SessionVWAP()
        self.cvd: deque[float] = deque(maxlen=500)
        self.cvd_total = 0.0
        self.book = OrderBook()
        self.tv: TradingViewAlert | None = None
        self.ctx: MarketContext | None = None
        self.signals: deque[Signal] = deque(maxlen=50)
        self.last_price = math.nan
        self.bar_count = 0  # total de barras cerradas (self.bars solo guarda las últimas)


class Engine:
    def __init__(self, cfg: Config, journal: Journal | None = None):
        self.cfg = cfg
        self.bar_ms = int(cfg.bar_seconds * 1000)
        self.journal = journal or Journal(resolve_path(cfg.storage.db_path, cfg.base_dir))
        self.calibrator = SetupCalibrator(cfg.decision.calibration_prior_strength, self.journal.load_calibration())
        self.decision = DecisionEngine(cfg.decision, cfg.risk, self.calibrator)
        self.broker = PaperBroker(cfg.risk, self.bar_ms, self.journal, self.calibrator)
        self.states: dict[str, SymbolState] = {}
        self.trading_enabled = True
        self.listeners: list = []  # callables(event: str, payload)

    def state(self, symbol: str) -> SymbolState:
        st = self.states.get(symbol)
        if st is None:
            st = self.states[symbol] = SymbolState(symbol, self.cfg)
        return st

    # ------------------------------------------------------------- entradas
    def on_trade(self, t: Trade) -> None:
        st = self.state(t.symbol)
        if st.bucket is None:
            st.bucket = auto_bucket(t.price)
        if st.bar is not None:
            if t.ts < st.bar.start:
                return  # tick atrasado (p. ej. solape entre historial y stream)
            if t.ts >= st.bar.end:
                self._close_bar(st)
        if st.bar is None:
            start = t.ts // self.bar_ms * self.bar_ms
            st.bar = FootprintBar(t.symbol, start, start + self.bar_ms, st.bucket)
        st.bar.add(t)
        st.vwap.add(t.price, t.qty, t.ts)
        st.last_price = t.price
        self.broker.on_price(t.symbol, t.price, t.ts)

    def on_book(self, snap: BookSnapshot) -> None:
        self.state(snap.symbol).book.update(snap)

    def on_tradingview(self, alert: TradingViewAlert) -> None:
        alert.symbol = normalize_symbol(alert.symbol)
        self.state(alert.symbol).tv = alert
        self._emit("tradingview", alert)

    def flush(self) -> None:
        """Cierra las barras en curso (fin de backtest)."""
        for st in self.states.values():
            if st.bar is not None:
                self._close_bar(st)

    # ------------------------------------------------------------- núcleo
    def _close_bar(self, st: SymbolState) -> None:
        bar = st.bar
        st.bar = None
        if bar is None or bar.volume <= 0:
            return
        st.bars.append(bar)
        st.bar_count += 1
        st.profile.add_bar(bar)
        st.cvd_total += bar.delta
        st.cvd.append(st.cvd_total)
        ctx = st.ctx = self._context(st, bar)
        self.broker.on_bar_close(st.symbol, bar, ctx)
        if not self.trading_enabled or len(st.bars) < self.cfg.warmup_bars:
            return
        signals = self.decision.evaluate(ctx)
        for s in signals:
            self.journal.log_signal(s)
            st.signals.append(s)
        best = self.decision.best(signals)
        if best and self.broker.open(best, bar.end):
            self._emit("entry", best)

    def _context(self, st: SymbolState, bar: FootprintBar) -> MarketContext:
        fp, q = self.cfg.footprint, self.cfg.quant
        bars = list(st.bars)
        poc, vah, val = st.profile.levels()
        stacked_buy, stacked_sell = bar.stacked(fp.imbalance_ratio, fp.stacked_min, fp.min_level_frac)
        tv_bias = 0
        ttl = self.cfg.decision.tradingview_ttl_seconds * 1000
        if st.tv and abs(bar.end - st.tv.ts) <= ttl:
            tv_bias = {"long": 1, "short": -1}.get(st.tv.side, 0)
        return MarketContext(
            symbol=st.symbol,
            bar=bar,
            bars=bars,
            atr=atr(bars, q.atr_period),
            hurst=hurst([b.close for b in bars[-q.hurst_window:]]),
            vwap=st.vwap.value,
            vwap_std=st.vwap.std,
            poc=poc,
            vah=vah,
            val=val,
            cvd=list(st.cvd),
            book_imbalance=st.book.imbalance(),
            tv_bias=tv_bias,
            stacked_buy=stacked_buy,
            stacked_sell=stacked_sell,
            stacked_min=fp.stacked_min,
        )

    def _emit(self, event: str, payload) -> None:
        for fn in self.listeners:
            fn(event, payload)

    # ------------------------------------------------------------- estado
    def snapshot(self) -> dict:
        fp = self.cfg.footprint
        symbols = {}
        for sym, st in self.states.items():
            pos = self.broker.positions.get(sym)
            symbols[sym] = {
                "price": st.last_price,
                "bucket": st.bucket,
                "bars_closed": st.bar_count,
                "current_bar": st.bar.to_dict(fp.imbalance_ratio, fp.min_level_frac) if st.bar else None,
                "recent_bars": [b.to_dict(fp.imbalance_ratio, fp.min_level_frac) for b in list(st.bars)[-3:]],
                "context": st.ctx.to_dict() if st.ctx else None,
                "book": {
                    "imbalance": st.book.imbalance(),
                    "spread": st.book.spread,
                    "walls": st.book.walls(),
                },
                "tradingview": vars(st.tv) if st.tv else None,
                "signals": [s.to_dict() for s in reversed(st.signals)][:20],
                "position": pos.to_dict(st.last_price) if pos else None,
            }
        return {
            "ts": int(time.time() * 1000),
            "trading_enabled": self.trading_enabled,
            "symbols": symbols,
            "stats": self.broker.stats(),
            "trades": self.broker.closed[-30:][::-1],
            "equity_curve": self.broker.equity_curve[-500:],
            "calibration": self.calibrator.summary(),
        }
