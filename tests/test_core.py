import math
import random

import pytest

from quantflow.config import ROOT, load_config
from quantflow.engine import Engine, normalize_symbol
from quantflow.models import Signal, Trade, TradingViewAlert
from quantflow.orderflow import FootprintBar, VolumeProfile, auto_bucket, nice_step
from quantflow.quant import SetupCalibrator, hurst
from quantflow.storage import Journal
from quantflow.strategy import DecisionEngine, MarketContext


def T(price, qty, buy, ts=0, sym="BTCUSDT"):
    return Trade(sym, ts, price, qty, buy)


@pytest.fixture
def cfg():
    c = load_config(ROOT / "config.yaml")
    c["warmup_bars"] = 5
    c["bar_seconds"] = 60
    return c


# ----------------------------------------------------------------- footprint
def test_nice_step_and_bucket():
    assert nice_step(7) == 10
    assert nice_step(0.35) == 0.5
    assert nice_step(2.2) == 2.5
    assert auto_bucket(100_000) == 10


def test_footprint_delta_poc_and_extremes():
    bar = FootprintBar("BTCUSDT", 0, 60_000, 10)
    for p, q, b in [(100, 1, True), (105, 2, False), (110, 5, True), (112, 1, False), (120, 0.5, True)]:
        bar.add(T(p, q, b))
    assert bar.volume == pytest.approx(9.5)
    assert bar.delta == pytest.approx(1 - 2 + 5 - 1 + 0.5)
    assert bar.min_delta == pytest.approx(-1)
    assert bar.poc() == pytest.approx(115)          # nivel 110 (6 uds), centro 115
    assert bar.levels[10] == [2.0, 1.0]             # nivel 100: bid 2, ask 1
    assert bar.extreme_volume("low", 1) == (2.0, 1.0)
    assert bar.open == 100 and bar.high == 120 and bar.low == 100 and bar.close == 120


def test_diagonal_and_stacked_imbalances():
    bar = FootprintBar("X", 0, 1, 1)
    # Niveles 10..14: compras agresivas fuertes que superan 3x al bid del nivel inferior
    for lvl in range(10, 15):
        bar.add(T(lvl, 1, False))       # bid = 1 en cada nivel
        bar.add(T(lvl, 5, True))        # ask = 5 en cada nivel
    buys, sells = bar.imbalances(3.0, 0.0)
    assert buys == [11, 12, 13, 14]      # nivel 10 no tiene nivel inferior con el que comparar
    assert sells == []
    zones_buy, zones_sell = bar.stacked(3.0, 3, 0.0)
    assert zones_buy == [(11, 15, 4)]
    assert zones_sell == []


# ----------------------------------------------------------------- perfil
def test_value_area_covers_70pct_and_contains_poc():
    prof = VolumeProfile(lookback=10, value_area=0.7)
    bar = FootprintBar("X", 0, 1, 1)
    for lvl, q in {95: 1, 96: 2, 97: 5, 98: 10, 99: 20, 100: 8, 101: 3, 102: 1}.items():
        bar.add(T(lvl, q, True))
    prof.add_bar(bar)
    poc, vah, val = prof.levels()
    assert poc == 99.5
    assert val <= 99 < vah
    covered = sum(q for lvl, q in {95: 1, 96: 2, 97: 5, 98: 10, 99: 20, 100: 8, 101: 3, 102: 1}.items()
                  if val <= lvl < vah)
    assert covered >= 0.7 * 50


def test_profile_rolls_off_old_bars():
    prof = VolumeProfile(lookback=1)
    a = FootprintBar("X", 0, 1, 1)
    a.add(T(100, 50, True))
    b = FootprintBar("X", 1, 2, 1)
    b.add(T(200, 1, True))
    prof.add_bar(a)
    prof.add_bar(b)
    assert prof.levels()[0] == 200.5


# ----------------------------------------------------------------- cuantitativo
def test_hurst_distinguishes_trend_from_mean_reversion():
    rnd = random.Random(1)
    trend, x = [], 100.0
    step = 0.0
    for _ in range(400):
        step = 0.9 * step + rnd.gauss(0, 0.1)  # incrementos persistentes
        x += step
        trend.append(math.exp(x / 100))
    mr = [math.exp((100 + rnd.gauss(0, 1)) / 100) for _ in range(400)]  # ruido alrededor de la media
    assert hurst(trend) > 0.6
    assert hurst(mr) < 0.2


def test_calibrator_shrinks_toward_observed_rate():
    cal = SetupCalibrator(prior_strength=30)
    assert cal.adjust("s", 0.6) == 0.6
    for _ in range(30):
        cal.record("s", 0.6, won=False)      # el setup nunca gana
    assert cal.adjust("s", 0.6) == pytest.approx(0.6 - 18 / 60)
    for _ in range(1000):
        cal.record("s", 0.6, won=False)
    assert cal.adjust("s", 0.6) < 0.05


# ----------------------------------------------------------------- decisión
def _absorption_ctx(tv_bias=0):
    """Vela con venta agresiva fuerte en el mínimo que cierra arriba, bajo el VWAP y el VAL."""
    bar = FootprintBar("BTCUSDT", 0, 60_000, 10)
    bar.add(T(1000, 1, True))
    bar.add(T(990, 30, False))     # venta agresiva absorbida en el mínimo
    bar.add(T(995, 5, False))
    bar.add(T(1008, 6, True))      # cierra en la parte alta
    return MarketContext(
        symbol="BTCUSDT", bar=bar, bars=[bar], atr=20.0, hurst=0.35, vwap=1060.0, vwap_std=25.0,
        poc=1050.0, vah=1080.0, val=995.0, cvd=[bar.delta], book_imbalance=0.3, tv_bias=tv_bias,
    )


def test_absorption_long_is_accepted_with_consistent_math(cfg):
    engine = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator())
    sigs = engine.evaluate(_absorption_ctx())
    longs = [s for s in sigs if s.setup == "absorcion" and s.side == "long"]
    assert len(longs) == 1
    s = longs[0]
    assert s.stop == pytest.approx(990 - 0.25 * 20)
    assert s.target == pytest.approx(1050.0)             # POC: nivel de valor más cercano con R:R suficiente
    risk = s.entry - s.stop
    assert s.rr == pytest.approx((s.target - s.entry) / risk)
    cost_win = s.entry * (0.05 + 0.02 + 2 * 0.01) / 100
    cost_loss = s.entry * (2 * 0.05 + 2 * 0.01) / 100
    ev = s.probability * (s.target - s.entry - cost_win) / risk - (1 - s.probability) * (risk + cost_loss) / risk
    assert s.expected_value_r == pytest.approx(ev)
    assert s.accepted, s.rejections
    assert DecisionEngine.best(sigs) is s


def test_tradingview_confirmation_raises_probability_and_can_be_required(cfg):
    engine = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator())
    p_none = engine.evaluate(_absorption_ctx(0))[0].probability
    p_with = engine.evaluate(_absorption_ctx(1))[0].probability
    p_against = engine.evaluate(_absorption_ctx(-1))[0].probability
    assert p_against < p_none < p_with
    cfg["decision"]["require_tradingview"] = True
    strict = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator())
    assert not strict.evaluate(_absorption_ctx(0))[0].accepted
    assert strict.evaluate(_absorption_ctx(1))[0].accepted


def test_stacked_imbalance_continuation(cfg):
    bar = FootprintBar("BTCUSDT", 0, 60_000, 10)
    for lvl in range(1000, 1060, 10):
        bar.add(T(lvl, 1, False))
        bar.add(T(lvl, 6, True))
    zb, zs = bar.stacked(3.0, 3, 0.5)
    ctx = MarketContext(symbol="BTCUSDT", bar=bar, bars=[bar], atr=30.0, hurst=0.65, vwap=1020.0,
                        vwap_std=25.0, poc=1000.0, vah=1040.0, val=990.0, cvd=[bar.delta],
                        book_imbalance=0.2, tv_bias=0, stacked_buy=zb, stacked_sell=zs)
    sigs = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator()).evaluate(ctx)
    s = next(s for s in sigs if s.setup == "imbalance_apilado")
    assert s.side == "long"
    assert s.stop == pytest.approx(zb[0][0] - 0.25 * 30)
    assert s.rr == pytest.approx(2.0)


# ----------------------------------------------------------------- broker
def _signal(side="long", entry=100.0, stop=95.0, target=110.0):
    return Signal("BTCUSDT", 0, "absorcion", side, entry, stop, target, 2.0, 0.6, 0.6, 0.5, True)


def test_broker_target_and_stop_r_multiples(cfg):
    cfg["risk"]["slippage_pct"] = 0.0
    cfg["risk"]["breakeven_at_r"] = 0
    e = Engine(cfg, journal=Journal())
    b = e.broker
    pos = b.open(_signal(), ts=0)
    assert pos.qty == pytest.approx(10_000 * 0.005 / 5)       # riesgo 0.5% / 5 de stop
    b.on_price("BTCUSDT", 111, ts=1)
    win = b.closed[-1]
    assert win["exit_reason"] == "objetivo"
    fees = 100 * pos.qty * 0.0005 + 110 * pos.qty * 0.0002   # entrada taker + objetivo maker
    assert win["fees"] == pytest.approx(fees)
    assert win["r_multiple"] == pytest.approx((10 * pos.qty - fees) / (5 * pos.qty))

    short_pos = b.open(_signal("short", 100, 105, 90), ts=10**9)
    b.on_price("BTCUSDT", 105, ts=10**9 + 1)
    loss = b.closed[-1]
    assert loss["exit_reason"] == "stop"
    loss_fees = (100 + 105) * short_pos.qty * 0.0005          # entrada y stop taker
    assert loss["fees"] == pytest.approx(loss_fees)
    assert loss["r_multiple"] == pytest.approx((-5 * short_pos.qty - loss_fees) / (5 * short_pos.qty))
    assert e.calibrator.stats["absorcion"]["n"] == 2


def test_broker_breakeven_cooldown_and_daily_limit(cfg):
    cfg["risk"]["slippage_pct"] = 0.0
    e = Engine(cfg, journal=Journal())
    b = e.broker
    b.open(_signal(), ts=0)
    b.on_price("BTCUSDT", 105.5, ts=1)                       # +1R -> stop a break-even
    assert b.positions["BTCUSDT"].breakeven
    b.on_price("BTCUSDT", 99.9, ts=2)
    assert b.closed[-1]["exit_reason"] == "break-even"
    assert b.block_reason("BTCUSDT", 3) == "enfriamiento tras la última operación"
    b.equity = b._day_start_equity * 0.97
    assert b.block_reason("BTCUSDT", 10**7) == "límite de pérdida diaria alcanzado"


def test_breakeven_never_above_market_and_gap_fill(cfg):
    cfg["risk"]["slippage_pct"] = 0.0
    e = Engine(cfg, journal=Journal())
    b = e.broker
    # riesgo 0.05: +1R (100.06) no cubre las comisiones (100.10) -> stop exactamente a la entrada
    b.open(_signal(entry=100.0, stop=99.95, target=100.5), ts=0)
    b.on_price("BTCUSDT", 100.06, ts=1)
    pos = b.positions["BTCUSDT"]
    assert pos.breakeven and pos.stop == pytest.approx(100.0)
    # gap a través del stop: se llena al precio real, no al del stop
    b.on_price("BTCUSDT", 99.0, ts=2)
    assert b.closed[-1]["exit"] == pytest.approx(99.0)


def test_rejects_stops_too_tight_for_costs(cfg):
    ctx = _absorption_ctx()
    ctx.atr = 0.5                       # stop ≈ 18.5 puntos sobre 1008 -> costes ~0.07R: pasa
    engine = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator())
    assert not any("comisiones" in r for r in engine.evaluate(ctx)[0].rejections)
    cfg["decision"]["max_cost_r"] = 0.05
    strict = DecisionEngine(cfg.decision, cfg.risk, SetupCalibrator())
    assert any("comisiones" in r for r in strict.evaluate(ctx)[0].rejections)


# ----------------------------------------------------------------- motor
def test_engine_builds_bars_and_runs_end_to_end(cfg):
    e = Engine(cfg, journal=Journal())
    rnd = random.Random(7)
    price, ts = 60_000.0, 1_700_000_000_000
    for _ in range(40_000):
        ts += rnd.randint(5, 40)
        price += rnd.gauss(0, 4)
        e.on_trade(Trade("BTCUSDT", ts, price, rnd.expovariate(5), rnd.random() < 0.5))
    e.flush()
    st = e.state("BTCUSDT")
    assert len(st.bars) >= 10
    assert all(b.end - b.start == 60_000 for b in st.bars)
    assert all(a.end <= b.start for a, b in zip(st.bars, list(st.bars)[1:], strict=False))
    assert st.cvd[-1] == pytest.approx(sum(b.delta for b in st.bars))
    snap = e.snapshot()
    assert snap["symbols"]["BTCUSDT"]["context"]["poc"] is not None


def test_tradingview_alert_symbol_normalization(cfg):
    assert normalize_symbol("BINANCE:BTCUSDT.P") == "BTCUSDT"
    assert normalize_symbol("ethusdtperp") == "ETHUSDT"
    e = Engine(cfg, journal=Journal())
    e.on_tradingview(TradingViewAlert("BINANCE:BTCUSDT.P", "long", "tv", 1.0, "1", 0))
    assert e.state("BTCUSDT").tv.side == "long"
