import asyncio
import zipfile
from datetime import date, timedelta

from quantflow.feeds.binance import _parse_agg, _parse_levels, days_back, download_day, iter_zip_trades


def test_parse_agg_inverts_maker_flag():
    buy = _parse_agg("BTCUSDT", {"T": "1700000000000", "p": "100.5", "q": "2", "m": False})
    assert buy.symbol == "BTCUSDT"
    assert buy.ts == 1_700_000_000_000
    assert buy.price == 100.5
    assert buy.qty == 2.0
    assert buy.is_buy is True

    sell = _parse_agg("BTCUSDT", {"T": "1700000000000", "p": "100.5", "q": "2", "m": True})
    assert sell.is_buy is False


def test_parse_levels_converts_strings_to_floats():
    assert _parse_levels([["100", "1.5"], ["99.5", "2"]]) == [(100.0, 1.5), (99.5, 2.0)]


def test_iter_zip_trades_futures_with_header_and_milliseconds(tmp_path):
    path = tmp_path / "futures.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "BTCUSDT-aggTrades-2026-01-02.csv",
            "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
            "1,100.5,2,10,11,1700000000000,true\n"
            "2,101,3,12,13,1700000001000,false\n",
        )
    trades = list(iter_zip_trades(path, "BTCUSDT"))
    assert len(trades) == 2
    assert (trades[0].ts, trades[0].price, trades[0].qty, trades[0].is_buy) == (1_700_000_000_000, 100.5, 2.0, False)
    assert (trades[1].ts, trades[1].is_buy) == (1_700_000_001_000, True)


def test_iter_zip_trades_spot_without_header_and_microseconds(tmp_path):
    path = tmp_path / "spot.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "BTCUSDT-aggTrades-2026-01-02.csv",
            "1,100.5,2,10,11,1700000000000000,true\n",
        )
    trade = next(iter_zip_trades(path, "BTCUSDT"))
    assert trade.ts == 1_700_000_000_000
    assert trade.is_buy is False


def test_days_back_returns_complete_previous_days():
    days = days_back(3)
    assert len(days) == 3
    assert days == sorted(days)
    assert days[-1] == date.today() - timedelta(days=1)


def test_download_day_uses_cache_without_network(tmp_path):
    day = date(2026, 1, 2)
    cached = tmp_path / f"futures-BTCUSDT-aggTrades-{day.isoformat()}.zip"
    cached.write_bytes(b"zip-en-cache")
    result = asyncio.run(download_day("futures", "BTCUSDT", day, tmp_path))
    assert result == cached
    assert cached.read_bytes() == b"zip-en-cache"
