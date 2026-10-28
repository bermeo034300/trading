"""Broker simulado (paper trading) con gestión de riesgo y de salidas.

Riesgo:
  - Tamaño por riesgo fijo: qty = equity · riesgo% / distancia al stop (con tope de apalancamiento).
  - Una posición por símbolo, enfriamiento tras cerrar y límite de pérdida diaria.
Salidas:
  - Stop y objetivo evaluados tick a tick (stop con slippage; objetivo como límite).
  - Break-even al alcanzar +N R.
  - Salida por order flow: imbalance apilado en contra con delta fuerte en contra.
  - Salida por tiempo tras N barras.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass

from ..models import Signal
from ..quant.calibration import SetupCalibrator
from ..storage import Journal

DAY_MS = 86_400_000


@dataclass
class Position:
    id: int
    symbol: str
    side: int
    setup: str
    entry: float
    stop: float
    initial_stop: float
    target: float
    qty: float
    opened_ts: int
    probability: float
    probability_model: float
    fees: float
    bars_held: int = 0
    breakeven: bool = False

    @property
    def risk(self) -> float:
        return abs(self.entry - self.initial_stop)

    def unrealized(self, price: float) -> float:
        return (price - self.entry) * self.side * self.qty

    def to_dict(self, price: float | None = None) -> dict:
        d = asdict(self)
        d["side"] = "long" if self.side > 0 else "short"
        if price is not None:
            d["price"] = price
            d["unrealized"] = self.unrealized(price)
            d["unrealized_r"] = self.unrealized(price) / (self.risk * self.qty) if self.risk else 0.0
        return d


class PaperBroker:
    def __init__(self, risk_cfg, bar_ms: int, journal: Journal, calibrator: SetupCalibrator):
        self.cfg = risk_cfg
        self.bar_ms = bar_ms
        self.journal = journal
        self.calibrator = calibrator
        self.fee = risk_cfg.fee_pct_per_side / 100
        self.maker_fee = risk_cfg.get("maker_fee_pct_per_side", risk_cfg.fee_pct_per_side) / 100
        self.slip = risk_cfg.slippage_pct / 100
        self.equity = float(risk_cfg.starting_equity)
        self.peak = self.equity
        self.max_drawdown = 0.0
        self.positions: dict[str, Position] = {}
        self.closed: list[dict] = []
        self.equity_curve: list[tuple[int, float]] = []
        self.last_close: dict[str, int] = {}
        self.last_price: dict[str, float] = {}
        self._ids = itertools.count(1)
        self._day: int | None = None
        self._day_start_equity = self.equity

    # ------------------------------------------------------------- apertura
    def block_reason(self, symbol: str, ts: int) -> str | None:
        self._roll_day(ts)
        if symbol in self.positions:
            return "ya hay una posición abierta"
        if self.equity <= self._day_start_equity * (1 - self.cfg.max_daily_loss_pct / 100):
            return "límite de pérdida diaria alcanzado"
        last = self.last_close.get(symbol)
        if last is not None and ts - last < self.cfg.cooldown_bars * self.bar_ms:
            return "enfriamiento tras la última operación"
        return None

    def open(self, sig: Signal, ts: int) -> Position | None:
        if self.block_reason(sig.symbol, ts):
            return None
        side = 1 if sig.side == "long" else -1
        fill = sig.entry * (1 + side * self.slip)
        risk_unit = (fill - sig.stop) * side
        if risk_unit <= 0:
            return None
        qty = self.equity * self.cfg.risk_per_trade_pct / 100 / risk_unit
        qty = min(qty, self.equity * self.cfg.max_leverage / fill)
        fee = fill * qty * self.fee
        self.equity -= fee
        pos = Position(next(self._ids), sig.symbol, side, sig.setup, fill, sig.stop, sig.stop, sig.target,
                       qty, ts, sig.probability, sig.probability_model, fee)
        self.positions[sig.symbol] = pos
        return pos

    # ------------------------------------------------------------- gestión
    def on_price(self, symbol: str, price: float, ts: int) -> None:
        self.last_price[symbol] = price
        pos = self.positions.get(symbol)
        if pos is None:
            return
        s = pos.side
        if (price - pos.stop) * s <= 0:
            # Orden stop a mercado: si el precio saltó el stop, se llena al precio real.
            fill = min(pos.stop, price) if s > 0 else max(pos.stop, price)
            self._close(pos, fill * (1 - s * self.slip), ts, "break-even" if pos.breakeven else "stop")
        elif (price - pos.target) * s >= 0:
            self._close(pos, pos.target, ts, "objetivo")
        elif (not pos.breakeven and self.cfg.breakeven_at_r > 0
              and (price - pos.entry) * s >= self.cfg.breakeven_at_r * pos.risk):
            be = pos.entry * (1 + s * 2 * self.fee)  # entrada + comisiones
            if (price - be) * s <= 0:                # el precio aún no las cubre: stop a la entrada
                be = pos.entry
            pos.stop = be
            pos.breakeven = True

    def on_bar_close(self, symbol: str, bar, ctx) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        pos.bars_held += 1
        s = pos.side
        exit_px = bar.close * (1 - s * self.slip)
        if pos.bars_held >= self.cfg.max_bars_in_trade:
            self._close(pos, exit_px, bar.end, "tiempo")
            return
        opposing = ctx.stacked_sell if s > 0 else ctx.stacked_buy
        if opposing and bar.delta_ratio * s <= -self.cfg.orderflow_exit_delta:
            self._close(pos, exit_px, bar.end, "order flow en contra")

    def _close(self, pos: Position, price: float, ts: int, reason: str) -> None:
        pnl = (price - pos.entry) * pos.side * pos.qty
        # El objetivo es una orden límite (maker); entrada, stops y salidas a mercado pagan taker.
        fee_pct = self.maker_fee if reason == "objetivo" else self.fee
        fee = price * pos.qty * fee_pct
        self.equity += pnl - fee
        fees = pos.fees + fee
        r = (pnl - fees) / (pos.risk * pos.qty) if pos.risk else 0.0
        record = {
            "symbol": pos.symbol, "setup": pos.setup, "side": "long" if pos.side > 0 else "short",
            "opened_ts": pos.opened_ts, "closed_ts": ts, "entry": pos.entry, "exit": price,
            "stop": pos.initial_stop, "target": pos.target, "qty": pos.qty,
            "pnl": pnl - fees, "fees": fees, "r_multiple": r, "probability": pos.probability,
            "exit_reason": reason,
        }
        del self.positions[pos.symbol]
        self.closed.append(record)
        self.last_close[pos.symbol] = ts
        self.journal.log_trade(record)
        self.calibrator.record(pos.setup, pos.probability_model, r > 0)
        self.journal.save_calibration(self.calibrator.stats)
        self.peak = max(self.peak, self.equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak - self.equity) / self.peak)
        self.equity_curve.append((ts, self.equity))

    def _roll_day(self, ts: int) -> None:
        day = ts // DAY_MS
        if day != self._day:
            self._day = day
            self._day_start_equity = self.equity

    # ------------------------------------------------------------- métricas
    def stats(self) -> dict:
        rs = [t["r_multiple"] for t in self.closed]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gross_win = sum(t["pnl"] for t in self.closed if t["pnl"] > 0)
        gross_loss = -sum(t["pnl"] for t in self.closed if t["pnl"] <= 0)
        start = float(self.cfg.starting_equity)
        return {
            "equity": self.equity,
            "return_pct": (self.equity / start - 1) * 100,
            "trades": len(rs),
            "win_rate": len(wins) / len(rs) if rs else None,
            "avg_win_r": sum(wins) / len(wins) if wins else None,
            "avg_loss_r": sum(losses) / len(losses) if losses else None,
            "expectancy_r": sum(rs) / len(rs) if rs else None,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
            "max_drawdown_pct": self.max_drawdown * 100,
            "open_positions": len(self.positions),
        }
