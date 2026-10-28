"""Motor de decisión: convierte candidatos en señales con probabilidad y esperanza.

  1. score = bias + Σ peso_i · factor_i         (modelo logístico)
  2. p_modelo = sigmoid(score)
  3. p = calibración bayesiana por setup (aprende de los resultados reales)
  4. EV (en R, neto de comisiones y slippage) = p · ganancia_R − (1 − p) · pérdida_R
  5. Se acepta si p, EV y R:R superan los mínimos y el stop es razonable
     (ni más amplio que N ATR ni tan ajustado que las comisiones se coman el riesgo).
     Si varias señales pasan, gana la de mayor EV.
"""
from __future__ import annotations

from ..models import Signal
from ..quant.calibration import SetupCalibrator
from ..quant.stats import sigmoid
from .setups import SETUPS, Candidate, MarketContext


class DecisionEngine:
    def __init__(self, decision_cfg, risk_cfg, calibrator: SetupCalibrator):
        self.p = decision_cfg
        self.risk = risk_cfg
        self.calibrator = calibrator
        unknown = set(decision_cfg.setups) - set(SETUPS)
        if unknown:
            raise ValueError(f"Setups desconocidos en config: {unknown}")

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        return [self._score(ctx, c) for name in self.p.setups for c in SETUPS[name](ctx, self.p)]

    @staticmethod
    def best(signals: list[Signal]) -> Signal | None:
        accepted = [s for s in signals if s.accepted]
        return max(accepted, key=lambda s: s.expected_value_r) if accepted else None

    def _score(self, ctx: MarketContext, c: Candidate) -> Signal:
        w = self.p.weights
        risk = abs(c.entry - c.stop)
        z = w.get("bias", 0.0) + sum(w.get(k, 0.0) * v for k, v in c.features.items())
        p_model = sigmoid(z)
        prob = self.calibrator.adjust(c.setup, p_model)

        cost = c.entry * (2 * self.risk.fee_pct_per_side + 2 * self.risk.slippage_pct) / 100
        reward = abs(c.target - c.entry)
        rr = reward / risk if risk > 0 else 0.0
        if risk > 0:
            gain_r = (reward - cost) / risk
            loss_r = (risk + cost) / risk
            ev = prob * gain_r - (1 - prob) * loss_r
        else:
            ev = -1.0

        rejections = []
        if risk <= 0:
            rejections.append("stop inválido")
        if prob < self.p.min_probability:
            rejections.append(f"probabilidad {prob:.0%} < {self.p.min_probability:.0%}")
        if ev < self.p.min_expected_value_r:
            rejections.append(f"EV {ev:+.2f}R < {self.p.min_expected_value_r:+.2f}R")
        if rr < self.p.min_rr:
            rejections.append(f"R:R {rr:.2f} < {self.p.min_rr}")
        if risk > 0 and cost / risk > self.p.max_cost_r:
            rejections.append(f"comisiones = {cost / risk:.2f}R (máx. {self.p.max_cost_r}R): stop demasiado ajustado")
        if risk > self.p.max_stop_atr * ctx.atr:
            rejections.append(f"stop de {risk / ctx.atr:.1f} ATR (máx. {self.p.max_stop_atr})")
        if self.p.require_tradingview and ctx.tv_bias * c.side <= 0:
            rejections.append("sin confirmación de TradingView")

        return Signal(
            symbol=ctx.symbol,
            ts=ctx.bar.end,
            setup=c.setup,
            side="long" if c.side > 0 else "short",
            entry=c.entry,
            stop=c.stop,
            target=c.target,
            rr=rr,
            probability=prob,
            probability_model=p_model,
            expected_value_r=ev,
            accepted=not rejections,
            features={k: round(v, 3) for k, v in c.features.items()},
            reasons=c.reasons,
            rejections=rejections,
        )
