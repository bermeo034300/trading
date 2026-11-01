import math
import random

import numpy as np
import pytest

from quantflow.config import load_config
from quantflow.models import Signal, Trade
from quantflow.research import OutcomeLabeler, analyze, auc, fit_logistic, predict_logistic, stats


@pytest.fixture
def risk():
    cfg = load_config()
    cfg["risk"]["slippage_pct"] = 0.0
    return cfg.risk


def _sig(side="long", entry=100.0, stop=95.0, target=110.0, ts=0, setup="absorcion"):
    return Signal("BTCUSDT", ts, setup, side, entry, stop, target, 2.0, 0.6, 0.6, 0.3, True,
                  features={"orderflow": 0.5, "delta": 0.1})


def test_labeler_target_stop_and_time(risk):
    lab = OutcomeLabeler(risk, bar_ms=60_000, max_bars=10)
    lab.add(_sig(), atr=5.0, hurst=0.5)
    lab.on_trade(Trade("BTCUSDT", 1, 111.0, 1.0, True))
    win = lab.rows[-1]
    assert win["exit_reason"] == "objetivo"
    assert win["outcome_r"] == pytest.approx((10 - 100 * 0.0005 - 110 * 0.0002) / 5)
    assert win["outcome_r"] == pytest.approx(win["gain_r"])

    lab.add(_sig("short", 100, 105, 90, ts=10), atr=5.0, hurst=0.5)
    lab.on_trade(Trade("BTCUSDT", 11, 106.0, 1.0, True))   # gap sobre el stop: se llena a 106
    loss = lab.rows[-1]
    assert loss["exit_reason"] == "stop"
    assert loss["outcome_r"] == pytest.approx((-6 - 100 * 0.0005 - 106 * 0.0005) / 5)

    lab.add(_sig(ts=20), atr=5.0, hurst=0.5)
    lab.on_trade(Trade("BTCUSDT", 20 + 600_000, 101.0, 1.0, True))
    assert lab.rows[-1]["exit_reason"] == "tiempo"
    assert lab.rows[-1]["minutes"] == pytest.approx(10)


def test_labeler_flags_overlapping_signals(risk):
    lab = OutcomeLabeler(risk, bar_ms=60_000, max_bars=10)
    lab.add(_sig(ts=0), 5.0, 0.5)
    lab.add(_sig(ts=60_000), 5.0, 0.5)                    # mismo setup y lado, la primera sigue abierta
    lab.add(_sig("short", 100, 120, 90, ts=60_000), 5.0, 0.5)
    lab.on_trade(Trade("BTCUSDT", 120_000, 111.0, 1.0, True))
    flags = {(r["side"], r["ts"]): r["overlap"] for r in lab.rows}
    assert flags == {("long", 0): 0, ("long", 60_000): 1}
    assert len(lab.open["BTCUSDT"]) == 1                  # el corto sigue abierto


def test_stats_t_test():
    s = stats([1.0, -1.0] * 50)
    assert s["mean_r"] == 0 and s["p_value"] == pytest.approx(0.5)
    s = stats([0.5 + 0.01 * i for i in range(100)])
    assert s["p_value"] < 1e-6 and s["ci_low"] > 0
    assert stats([])["n"] == 0


def test_logistic_recovers_signal_and_auc():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.3 + 1.5 * X[:, 0])))).astype(float)
    w = fit_logistic(X, y, l2=0.1)
    assert w[1] == pytest.approx(1.5, abs=0.15)
    assert abs(w[2]) < 0.15
    assert auc(y, predict_logistic(w, X)) > 0.75
    assert auc(y, rng.random(4000)) == pytest.approx(0.5, abs=0.05)


def test_analyze_finds_edge_only_where_it_exists():
    rnd = random.Random(3)
    rows = []
    for i in range(3000):
        setup = "absorcion" if i % 2 else "divergencia_cvd"
        of = rnd.uniform(0, 1)
        # absorción gana más cuando el order flow es fuerte; divergencia es ruido puro
        p = 0.35 + 0.4 * of if setup == "absorcion" else 0.4
        win = rnd.random() < p
        rows.append({
            "ts": i * 300_000, "date": f"d{i}", "symbol": "BTCUSDT", "setup": setup,
            "side": "long" if i % 3 else "short", "accepted": int(of > 0.6), "overlap": 0,
            "p_model": 0.5 + 0.2 * of, "gain_r": 1.8, "loss_r": -1.1,
            "outcome_r": 1.8 if win else -1.1, "win": int(win), "exit_reason": "objetivo" if win else "stop",
            "orderflow": of, "delta": rnd.uniform(-1, 1), "location": 0.0, "regime": 0.0,
            "book": 0.0, "tradingview": 0.0,
        })
    res = analyze(rows, train_frac=0.6, min_ev=0.15)
    assert res["muestras"]["ajuste"] == 1800 and res["muestras"]["prueba"] == 1200
    assert res["modelo_ajustado"]["auc_prueba"] > 0.55
    assert res["modelo_ajustado"]["prueba_ev_min"]["mean_r"] > 0.2
    assert res["todas_las_señales"]["prueba"]["divergencia_cvd"]["p_value"] > 0.05
    assert "book" not in res["modelo_ajustado"]["pesos"]          # constante en histórico: se descarta
    assert not math.isnan(res["regla_actual"]["prueba"]["mean_r"])
