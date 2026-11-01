import asyncio

import pytest

from quantflow.backtest import _by_setup, _merge, run_backtest
from quantflow.config import ROOT, load_config
from quantflow.models import Trade


def _trades(ts_list: list[int]) -> list[Trade]:
    return [Trade("BTCUSDT", ts, 100.0, 1.0, True) for ts in ts_list]


def test_merge_orders_multiple_sources_by_timestamp():
    async def source(items):
        for item in items:
            yield item

    async def collect():
        return [t async for t in _merge([source(_trades([1, 3])), source(_trades([2, 4]))])]

    assert [t.ts for t in asyncio.run(collect())] == [1, 2, 3, 4]


def test_merge_handles_empty_sources():
    async def empty():
        if False:
            yield

    async def collect():
        return [t async for t in _merge([empty(), empty()])]

    assert asyncio.run(collect()) == []


def test_by_setup_aggregates_metrics():
    trades = [
        {"setup": "absorcion", "r_multiple": 2.0},
        {"setup": "absorcion", "r_multiple": -1.0},
        {"setup": "imbalance_apilado", "r_multiple": 1.0},
    ]
    result = _by_setup(trades)
    assert result["absorcion"] == {"trades": 2, "win_rate": 0.5, "expectancy_r": 0.5, "total_r": 1.0}
    assert result["imbalance_apilado"] == {"trades": 1, "win_rate": 1.0, "expectancy_r": 1.0, "total_r": 1.0}


def test_run_backtest_processes_ticks_without_touching_the_network(monkeypatch, tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg["bar_seconds"] = 60
    cfg["warmup_bars"] = 3

    async def fake_fetch(market, symbol, start_ms, end_ms):
        start = 1_700_000_000_000
        for i in range(600):
            yield Trade(symbol, start + i * 1000, 100.0, 1.0, i % 2 == 0)

    monkeypatch.setattr("quantflow.backtest.binance.fetch_agg_trades", fake_fetch)
    out = tmp_path / "trades.csv"

    result = asyncio.run(run_backtest(cfg, ["BTCUSDT"], hours=1, out_csv=str(out)))

    assert result["stats"]["trades"] == 0
    assert result["stats"]["equity"] == 10_000
    assert result["trades"] == []
    assert not out.exists()  # sin operaciones no se escribe CSV


def test_run_backtest_requires_hours_or_days():
    with pytest.raises(ValueError):
        asyncio.run(run_backtest(load_config(ROOT / "config.yaml"), ["BTCUSDT"]))


def test_vision_source_skips_days_not_yet_published(monkeypatch, tmp_path):
    from datetime import date

    from quantflow import backtest
    from quantflow.feeds import binance

    days = [date(2026, 1, 1), date(2026, 1, 2)]
    monkeypatch.setattr(binance, "days_back", lambda n: days)

    async def fake_download(market, symbol, day, cache_dir):
        if day == days[-1]:
            raise binance.DayNotPublished(day.isoformat())
        return tmp_path / "dia1.zip"

    monkeypatch.setattr(binance, "download_day", fake_download)
    monkeypatch.setattr(binance, "iter_zip_trades", lambda path, symbol: iter([Trade(symbol, 1, 100.0, 1.0, True)]))

    async def collect():
        return [t async for t in backtest._vision_source("futures", "BTCUSDT", 2, tmp_path)]

    assert [t.ts for t in asyncio.run(collect())] == [1]
