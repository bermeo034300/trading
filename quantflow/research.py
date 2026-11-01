"""Validación estadística de la ventaja.

Un backtest normal da pocas operaciones (una posición a la vez, enfriamientos...), así que
no alcanza para saber si hay ventaja. Aquí se hace otra cosa:

1. Se recorren ticks históricos con el mismo motor y se registran TODAS las señales
   evaluadas (aceptadas o no) con sus factores.
2. Cada señal se etiqueta con su resultado real: se simula la operación con los ticks
   siguientes hasta el stop, el objetivo o el límite de tiempo, con comisiones y slippage,
   sin importar si había otra posición abierta (barrera triple).
3. Se separa cronológicamente en AJUSTE (primeros días) y PRUEBA (últimos días). Lo que se
   aprende o elige con los datos de ajuste se mide solo en los de prueba: es la única forma
   honesta de estimar cómo se comportaría con datos que el modelo nunca vio.

Las señales de barras consecutivas del mismo setup se solapan (son casi la misma operación),
así que las estadísticas usan solo señales sin solape para no inflar la muestra.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .backtest import _vision_source
from .config import Config, load_config, resolve_path
from .engine import Engine
from .models import Signal, Trade
from .storage import Journal

log = logging.getLogger("quantflow.research")

FEATURES = ["orderflow", "delta", "location", "regime", "book", "tradingview"]


# ===================================================================== etiquetado
@dataclass
class _Virtual:
    row: dict
    side: int
    entry: float
    stop: float
    target: float
    risk: float
    expires: int


class OutcomeLabeler:
    """Simula cada señal como si se hubiera operado y anota su resultado en R (neto de costes).

    Entrada a mercado (taker + slippage). Salidas: objetivo como límite (maker, sin slippage),
    stop y tiempo a mercado (taker + slippage). Igual que el paper broker, pero sin break-even
    ni salida por order flow, para que la etiqueta dependa solo de la señal.
    """

    def __init__(self, risk_cfg, bar_ms: int, max_bars: int):
        self.taker = risk_cfg.fee_pct_per_side / 100
        self.maker = risk_cfg.get("maker_fee_pct_per_side", risk_cfg.fee_pct_per_side) / 100
        self.slip = risk_cfg.slippage_pct / 100
        self.hold_ms = bar_ms * max_bars
        self.open: dict[str, list[_Virtual]] = defaultdict(list)
        self.rows: list[dict] = []

    def add(self, sig: Signal, atr: float, hurst: float) -> None:
        s = 1 if sig.side == "long" else -1
        fill = sig.entry * (1 + s * self.slip)
        risk = (fill - sig.stop) * s
        if risk <= 0:
            return
        overlaps = any(v.row["setup"] == sig.setup and v.side == s for v in self.open[sig.symbol])
        entry_fee = fill * self.taker
        row = {
            "ts": sig.ts,
            "date": datetime.fromtimestamp(sig.ts / 1000, UTC).strftime("%Y-%m-%d %H:%M"),
            "symbol": sig.symbol,
            "setup": sig.setup,
            "side": sig.side,
            "accepted": int(sig.accepted),
            "overlap": int(overlaps),
            "p_model": sig.probability_model,
            "ev_model": sig.expected_value_r,
            "rr": sig.rr,
            "risk_atr": risk / atr if atr > 0 else math.nan,
            "cost_r": (entry_fee + fill * self.taker) / risk,
            # resultado si toca el objetivo / si toca el stop, en R netos
            "gain_r": ((sig.target - fill) * s - entry_fee - sig.target * self.maker) / risk,
            "loss_r": -((fill - sig.stop * (1 - s * self.slip)) * s + entry_fee + sig.stop * self.taker) / risk,
            "hurst": hurst,
            **{f: sig.features.get(f, 0.0) for f in FEATURES},
        }
        self.open[sig.symbol].append(_Virtual(row, s, fill, sig.stop, sig.target, risk, sig.ts + self.hold_ms))

    def on_trade(self, t: Trade) -> None:
        vts = self.open.get(t.symbol)
        if not vts:
            return
        keep = []
        for v in vts:
            s = v.side
            if (t.price - v.stop) * s <= 0:
                fill = min(v.stop, t.price) if s > 0 else max(v.stop, t.price)
                self._finish(v, fill * (1 - s * self.slip), self.taker, "stop", t.ts)
            elif (t.price - v.target) * s >= 0:
                self._finish(v, v.target, self.maker, "objetivo", t.ts)
            elif t.ts >= v.expires:
                self._finish(v, t.price * (1 - s * self.slip), self.taker, "tiempo", t.ts)
            else:
                keep.append(v)
        self.open[t.symbol] = keep

    def _finish(self, v: _Virtual, exit_px: float, fee: float, reason: str, ts: int) -> None:
        pnl = (exit_px - v.entry) * v.side - v.entry * self.taker - exit_px * fee
        v.row.update(outcome_r=pnl / v.risk, win=int(pnl > 0), exit_reason=reason,
                     minutes=(ts - v.row["ts"]) / 60_000)
        self.rows.append(v.row)


# ===================================================================== recolección
async def _collect_symbol(cfg: Config, symbol: str, days: int) -> list[dict]:
    engine = Engine(cfg, journal=Journal(":memory:"))
    engine.calibrator.stats = {}
    engine.calibrator.k = float("inf")  # sin calibración: se estudia la salida cruda del modelo
    labeler = OutcomeLabeler(cfg.risk, engine.bar_ms, cfg.risk.max_bars_in_trade)

    def on_event(event: str, payload) -> None:
        if event == "signals":
            ctx = engine.state(payload[0].symbol).ctx
            assert ctx is not None  # se emite justo después de calcular el contexto
            for sig in payload:
                labeler.add(sig, ctx.atr, ctx.hurst)

    engine.listeners.append(on_event)
    cache_dir = resolve_path(cfg.storage.cache_dir, cfg.base_dir)
    n = 0
    async for t in _vision_source(cfg.exchange.market, symbol, days, cache_dir):
        engine.on_trade(t)
        labeler.on_trade(t)
        n += 1
        if n % 2_000_000 == 0:
            log.info("%s: %s ticks, %d señales etiquetadas", symbol, f"{n:,}", len(labeler.rows))
    log.info("%s: %s ticks, %d barras, %d señales", symbol, f"{n:,}", engine.state(symbol).bar_count,
             len(labeler.rows))
    return labeler.rows  # las que siguen abiertas al final no tienen resultado y se descartan


def _collect_worker(config_path: str, symbol: str, days: int) -> list[dict]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(_collect_symbol(load_config(config_path), symbol, days))


def collect(config_path: str, symbols: list[str], days: int, out_csv: Path) -> list[dict]:
    """Un proceso por símbolo (son independientes), luego se guarda todo en un CSV."""
    with ProcessPoolExecutor(max_workers=len(symbols)) as pool:
        parts = list(pool.map(_collect_worker, [config_path] * len(symbols), symbols, [days] * len(symbols)))
    rows = sorted((r for part in parts for r in part), key=lambda r: r["ts"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


def load_dataset(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            if k not in ("date", "symbol", "setup", "side", "exit_reason"):
                r[k] = float(v) if v not in ("", "nan") else math.nan
    return rows


# ===================================================================== estadística
def stats(rs) -> dict:
    """Resumen de una serie de resultados en R, con prueba t de que la media es > 0."""
    a = np.asarray(rs, dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0}
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    se = sd / math.sqrt(n) if n > 1 else math.nan
    t = mean / se if se and se > 0 else math.nan
    wins, losses = float(a[a > 0].sum()), float(-a[a <= 0].sum())
    return {
        "n": n,
        "win_rate": float((a > 0).mean()),
        "mean_r": mean,
        "ci_low": mean - 1.96 * se if n > 1 else math.nan,
        "ci_high": mean + 1.96 * se if n > 1 else math.nan,
        "t": t,
        "p_value": 0.5 * math.erfc(t / math.sqrt(2)) if not math.isnan(t) else math.nan,  # H1: media > 0
        "total_r": float(a.sum()),
        "profit_factor": wins / losses if losses > 0 else math.nan,
    }


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 100) -> np.ndarray:
    """Regresión logística con L2 por Newton-Raphson (IRLS). Devuelve [intercepto, pesos...]."""
    X1 = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(X1.shape[1])
    reg = np.eye(X1.shape[1]) * l2
    reg[0, 0] = 0.0  # el intercepto no se penaliza
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X1 @ w, -30, 30)))
        g = X1.T @ (p - y) + reg @ w
        H = X1.T @ (X1 * (p * (1 - p))[:, None]) + reg
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def predict_logistic(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(w[0] + X @ w[1:], -30, 30)))


def auc(y: np.ndarray, score: np.ndarray) -> float:
    """Área bajo la curva ROC (Mann-Whitney). 0.5 = el modelo no distingue ganadoras de perdedoras."""
    pos, neg = score[y == 1], score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return math.nan
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    n_pos, n_neg = float(len(pos)), float(len(neg))
    return float((ranks[: len(pos)].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _design(rows: list[dict], cols: list[str], setups: list[str]) -> np.ndarray:
    feats = np.array([[r[c] for c in cols] for r in rows], float).reshape(len(rows), len(cols))
    dummies = np.array([[1.0 if r["setup"] == s else 0.0 for s in setups[1:]] for r in rows], float)
    dummies = dummies.reshape(len(rows), len(setups) - 1)
    side = np.array([1.0 if r["side"] == "long" else -1.0 for r in rows], float)
    return np.column_stack([feats, dummies, side])


def analyze(rows: list[dict], train_frac: float = 0.6, min_ev: float = 0.15) -> dict:
    rows = [r for r in rows if not math.isnan(r.get("outcome_r", math.nan))]
    indep = [r for r in rows if not r["overlap"]]
    if len(indep) < 20:
        raise ValueError(f"Solo hay {len(indep)} señales sin solape: hace falta un periodo más largo.")
    t0, t1 = indep[0]["ts"], indep[-1]["ts"]
    split_ts = t0 + (t1 - t0) * train_frac
    train = [r for r in indep if r["ts"] < split_ts]
    test = [r for r in indep if r["ts"] >= split_ts]

    def by_group(subset):
        groups: dict[str, list[float]] = defaultdict(list)
        for r in subset:
            groups[f"{r['setup']} {r['side']}"].append(r["outcome_r"])
            groups[r["setup"]].append(r["outcome_r"])
        return {k: stats(v) for k, v in sorted(groups.items())}

    # 1. Calibración del modelo actual: ¿la probabilidad que da se cumple?
    bins = [0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 1.0]
    calib = []
    for lo, hi in zip(bins, bins[1:], strict=False):
        sel = [r for r in indep if lo <= r["p_model"] < hi]
        if sel:
            calib.append({"rango": f"{lo:.2f}-{hi:.2f}", "n": len(sel),
                          "p_media": float(np.mean([r["p_model"] for r in sel])),
                          "acierto_real": float(np.mean([r["win"] for r in sel]))})

    # 2. Modelo re-ajustado con los datos de AJUSTE y medido en PRUEBA
    setups = sorted({r["setup"] for r in indep})
    cols = [c for c in FEATURES if np.std([r[c] for r in train]) > 1e-9]  # sin libro/TV en histórico
    Xtr, Xte = _design(train, cols, setups), _design(test, cols, setups)
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd == 0] = 1.0
    ytr = np.array([r["win"] for r in train], float)
    yte = np.array([r["win"] for r in test], float)
    w = fit_logistic((Xtr - mu) / sd, ytr)
    p_tr = predict_logistic(w, (Xtr - mu) / sd)
    p_te = predict_logistic(w, (Xte - mu) / sd)

    def ev(p, subset):
        return np.array([pi * r["gain_r"] + (1 - pi) * r["loss_r"] for pi, r in zip(p, subset, strict=True)])

    ev_te = ev(p_te, test)
    ev_tr = ev(p_tr, train)
    base_rate = float(ytr.mean())

    def brier(p, y):
        return float(np.mean((p - y) ** 2))

    weekly: dict[str, list[float]] = defaultdict(list)
    for r, e in zip(test, ev_te, strict=True):
        if e >= min_ev:
            weekly[datetime.fromtimestamp(r["ts"] / 1000, UTC).strftime("%G-S%V")].append(r["outcome_r"])

    names = cols + [f"setup={s}" for s in setups[1:]] + ["lado_largo"]
    return {
        "periodo": {"desde": indep[0]["date"], "hasta": indep[-1]["date"],
                    "corte": datetime.fromtimestamp(split_ts / 1000, UTC).strftime("%Y-%m-%d %H:%M")},
        "muestras": {"señales": len(rows), "sin_solape": len(indep), "ajuste": len(train), "prueba": len(test)},
        "todas_las_señales": {"ajuste": by_group(train), "prueba": by_group(test)},
        "regla_actual": {
            "ajuste": stats([r["outcome_r"] for r in train if r["accepted"]]),
            "prueba": stats([r["outcome_r"] for r in test if r["accepted"]]),
            "prueba_por_setup": by_group([r for r in test if r["accepted"]]),
        },
        "calibracion_actual": calib,
        "modelo_ajustado": {
            "pesos": {n: float(v) for n, v in zip(["intercepto", *names], w, strict=True)},
            "auc_ajuste": auc(ytr, p_tr),
            "auc_prueba": auc(yte, p_te),
            "auc_prueba_modelo_actual": auc(yte, np.array([r["p_model"] for r in test])),
            "brier_prueba": brier(p_te, yte),
            "brier_prueba_tasa_base": brier(np.full(len(yte), base_rate), yte),
            "ajuste_ev_min": stats([r["outcome_r"] for r, e in zip(train, ev_tr, strict=True) if e >= min_ev]),
            "prueba_ev_min": stats([r["outcome_r"] for r, e in zip(test, ev_te, strict=True) if e >= min_ev]),
            "prueba_por_semana": {k: stats(v) for k, v in sorted(weekly.items())},
        },
        "salidas": {k: sum(1 for r in indep if r["exit_reason"] == k) for k in ("objetivo", "stop", "tiempo")},
    }


# ===================================================================== informe
def _fmt_stats(s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return "sin operaciones"
    return (f"n={s['n']:>5}  acierto {s['win_rate']:.0%}  media {s['mean_r']:+.3f}R "
            f"[IC95 {s['ci_low']:+.3f}, {s['ci_high']:+.3f}]  t={s['t']:+.2f}  p={s['p_value']:.3f}  "
            f"total {s['total_r']:+.1f}R")


def render_report(res: dict, min_ev: float) -> str:
    out = []
    p, m = res["periodo"], res["muestras"]
    out.append("# Validación de la ventaja de QuantFlow\n")
    out.append(f"Periodo: {p['desde']} → {p['hasta']} (UTC). Ajuste hasta {p['corte']}, prueba después.\n")
    out.append(f"Señales etiquetadas: {m['señales']:,} · sin solape: {m['sin_solape']:,} "
               f"(ajuste {m['ajuste']:,} / prueba {m['prueba']:,})\n")
    out.append("Resultados en R netos de comisiones y slippage. p = probabilidad de ver una media así "
               "de alta si la ventaja real fuera cero (p < 0.05 ≈ significativo).\n")

    out.append("\n## 1. Todas las señales, sin filtro (¿el patrón en sí tiene ventaja?)\n")
    for part in ("ajuste", "prueba"):
        out.append(f"\n**{part.capitalize()}**\n```")
        for k, s in res["todas_las_señales"][part].items():
            out.append(f"{k:<28} {_fmt_stats(s)}")
        out.append("```")

    r = res["regla_actual"]
    out.append("\n## 2. Regla actual (pesos y umbrales de config.yaml)\n```")
    out.append(f"ajuste  {_fmt_stats(r['ajuste'])}")
    out.append(f"prueba  {_fmt_stats(r['prueba'])}")
    out.append("```\nEn prueba, por setup:\n```")
    for k, s in r["prueba_por_setup"].items():
        out.append(f"{k:<28} {_fmt_stats(s)}")
    out.append("```")

    out.append("\n## 3. ¿La probabilidad del modelo actual se cumple?\n```")
    out.append(f"{'rango p':<12}{'n':>7}{'p media':>10}{'acierto real':>14}")
    for c in res["calibracion_actual"]:
        out.append(f"{c['rango']:<12}{c['n']:>7}{c['p_media']:>10.0%}{c['acierto_real']:>14.0%}")
    out.append("```")

    mm = res["modelo_ajustado"]
    out.append("\n## 4. Modelo re-ajustado con datos de ajuste, medido en prueba\n")
    out.append(f"AUC (0.5 = azar): ajuste {mm['auc_ajuste']:.3f} · prueba {mm['auc_prueba']:.3f} · "
               f"modelo actual en prueba {mm['auc_prueba_modelo_actual']:.3f}\n")
    out.append(f"Brier en prueba (menor es mejor): modelo {mm['brier_prueba']:.4f} vs "
               f"tasa base {mm['brier_prueba_tasa_base']:.4f}\n")
    out.append(f"\nOperando solo si EV ≥ {min_ev:+.2f}R:\n```")
    out.append(f"ajuste  {_fmt_stats(mm['ajuste_ev_min'])}")
    out.append(f"prueba  {_fmt_stats(mm['prueba_ev_min'])}")
    out.append("```\nPrueba por semana:\n```")
    for k, s in mm["prueba_por_semana"].items():
        out.append(f"{k}  {_fmt_stats(s)}")
    out.append("```\nPesos ajustados (factores estandarizados; + favorece ganar):\n```")
    for k, v in mm["pesos"].items():
        out.append(f"{k:<28} {v:+.3f}")
    out.append("```")
    s = res["salidas"]
    out.append(f"\nSalidas (sin solape): objetivo {s['objetivo']:,} · stop {s['stop']:,} · tiempo {s['tiempo']:,}\n")
    return "\n".join(out)
