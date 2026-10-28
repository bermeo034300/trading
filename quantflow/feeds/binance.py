"""Datos de Binance: ticks agregados con lado agresor y libro L2, en vivo e históricos.

- Tiempo real: WebSocket combinado <symbol>@aggTrade + <symbol>@depth20@100ms.
- Histórico corto (horas): REST /aggTrades paginado por id.
- Histórico largo (días): archivos diarios de data.binance.vision (zip + CSV), con caché local.

En aggTrade, m=True significa que el comprador era el maker, es decir, el agresor vendió.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import websockets

from ..models import BookSnapshot, Trade

log = logging.getLogger("quantflow.binance")

WS_BASE = {
    "futures": "wss://fstream.binance.com/stream?streams=",
    "spot": "wss://stream.binance.com:9443/stream?streams=",
}
REST_AGG = {
    "futures": "https://fapi.binance.com/fapi/v1/aggTrades",
    "spot": "https://api.binance.com/api/v3/aggTrades",
}
VISION = {
    "futures": "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip",
    "spot": "https://data.binance.vision/data/spot/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip",
}
HOUR_MS = 3_600_000


def _parse_agg(symbol: str, d: dict) -> Trade:
    return Trade(symbol, int(d["T"]), float(d["p"]), float(d["q"]), not d["m"])


def _parse_levels(levels) -> list[tuple[float, float]]:
    return [(float(p), float(q)) for p, q in levels]


# ----------------------------------------------------------------- tiempo real
async def stream(market: str, symbols: list[str], on_trade: Callable[[Trade], None],
                 on_book: Callable[[BookSnapshot], None], stop: asyncio.Event) -> None:
    names = "/".join(f"{s.lower()}@aggTrade/{s.lower()}@depth20@100ms" for s in symbols)
    url = WS_BASE[market] + names
    backoff = 1
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2 ** 22) as ws:
                log.info("Conectado a Binance %s: %s", market, ", ".join(symbols))
                backoff = 1
                async for raw in ws:
                    if stop.is_set():
                        break
                    msg = json.loads(raw)
                    name, data = msg.get("stream", ""), msg.get("data", {})
                    symbol = name.split("@")[0].upper()
                    if "@aggTrade" in name:
                        on_trade(_parse_agg(symbol, data))
                    elif "@depth" in name:
                        bids = data.get("b", data.get("bids", []))
                        asks = data.get("a", data.get("asks", []))
                        on_book(BookSnapshot(symbol, int(data.get("E", 0)), _parse_levels(bids), _parse_levels(asks)))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - reconexión ante cualquier fallo de red
            log.warning("WebSocket desconectado (%s). Reintentando en %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ----------------------------------------------------------------- REST
async def _get(client: httpx.AsyncClient, url: str, params: dict) -> list:
    for attempt in range(8):
        r = await client.get(url, params=params)
        if r.status_code in (418, 429):
            wait = int(r.headers.get("Retry-After", 10))
            log.warning("Límite de peticiones de Binance, esperando %ss", wait)
            await asyncio.sleep(wait)
            continue
        if r.status_code >= 500:
            await asyncio.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0))
        if used > 1800:
            await asyncio.sleep(5)
        return r.json()
    raise RuntimeError(f"Binance no respondió tras varios intentos: {url}")


async def fetch_agg_trades(market: str, symbol: str, start_ms: int, end_ms: int) -> AsyncIterator[Trade]:
    """Ticks agregados entre start_ms y end_ms, en orden."""
    url = REST_AGG[market]
    async with httpx.AsyncClient(timeout=30) as client:
        # Primer tramo por tiempo (máx. 1 h por petición) hasta encontrar el primer tick.
        window = start_ms
        rows: list = []
        while window <= end_ms and not rows:
            rows = await _get(client, url, {"symbol": symbol, "startTime": window,
                                            "endTime": min(window + HOUR_MS - 1, end_ms), "limit": 1000})
            window += HOUR_MS
        # Después, paginación por id.
        while rows:
            for d in rows:
                if d["T"] > end_ms:
                    return
                yield _parse_agg(symbol, d)
            if len(rows) < 1000:
                return
            rows = await _get(client, url, {"symbol": symbol, "fromId": rows[-1]["a"] + 1, "limit": 1000})


# ----------------------------------------------------------------- data.binance.vision
async def download_day(market: str, symbol: str, day: date, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{market}-{symbol}-aggTrades-{day.isoformat()}.zip"
    if path.exists():
        return path
    url = VISION[market].format(s=symbol, d=day.isoformat())
    log.info("Descargando %s", url)
    tmp = path.with_suffix(".part")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
    tmp.replace(path)
    return path


def iter_zip_trades(path: Path, symbol: str) -> Iterator[Trade]:
    """CSV de aggTrades: id, precio, cantidad, primer_id, último_id, timestamp, is_buyer_maker[, best_match].
    Futuros trae cabecera; spot no y desde 2025 usa microsegundos."""
    with zipfile.ZipFile(path) as zf:
        with zf.open(zf.namelist()[0]) as raw:
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                ts = int(row[5])
                if ts > 10 ** 14:
                    ts //= 1000
                yield Trade(symbol, ts, float(row[1]), float(row[2]), row[6].lower() != "true")


def days_back(n: int) -> list[date]:
    """Los últimos n días completos (UTC); los archivos del día en curso aún no existen."""
    today = date.today()
    return [today - timedelta(days=i) for i in range(n, 0, -1)]
