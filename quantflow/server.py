"""Servidor en vivo: stream de Binance, webhook de TradingView y panel web."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import Config
from .engine import Engine
from .feeds import binance
from .models import TradingViewAlert

log = logging.getLogger("quantflow.server")
DASHBOARD = Path(__file__).parent / "dashboard" / "index.html"
DEFAULT_PASSPHRASE = "cambia-esto"


def _configured_passphrase(cfg: Config) -> str:
    return str(cfg.server.webhook_passphrase or "").strip()


def webhook_enabled(cfg: Config) -> bool:
    """El webhook queda apagado mientras siga la passphrase de ejemplo."""
    passphrase = _configured_passphrase(cfg)
    return bool(passphrase) and passphrase != DEFAULT_PASSPHRASE


def _clean(o):
    """JSON no admite NaN/inf: se convierten en null."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


async def run_feed(cfg: Config, engine: Engine, stop: asyncio.Event) -> None:
    market, symbols = cfg.exchange.market, list(cfg.symbols)
    if cfg.warmup_minutes:
        engine.trading_enabled = False
        end = int(time.time() * 1000)
        start = end - int(cfg.warmup_minutes * 60_000)
        for sym in symbols:
            log.info("Calentando %s con %s min de ticks históricos...", sym, cfg.warmup_minutes)
            try:
                async for t in binance.fetch_agg_trades(market, sym, start, end):
                    engine.on_trade(t)
            except Exception as e:  # noqa: BLE001 - sin historial se arranca en frío
                log.warning("No se pudo descargar el historial de %s: %s", sym, e)
        engine.trading_enabled = True
        log.info("Calentamiento completo. Escuchando el mercado en vivo.")
    await binance.stream(market, symbols, engine.on_trade, engine.on_book, stop)


def create_app(cfg: Config, engine: Engine | None = None, start_feed: bool = True) -> FastAPI:
    engine = engine or Engine(cfg)
    stop = asyncio.Event()
    expected_passphrase = _configured_passphrase(cfg)
    webhook_ready = webhook_enabled(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(run_feed(cfg, engine, stop)) if start_feed else None
        if not webhook_ready:
            log.warning("Webhook de TradingView deshabilitado: cambia server.webhook_passphrase en config.yaml.")
        yield
        stop.set()
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="QuantFlow", lifespan=lifespan)
    app.state.engine = engine

    @app.get("/")
    async def dashboard():
        return FileResponse(DASHBOARD)

    @app.get("/api/state")
    async def state():
        return JSONResponse(_clean(engine.snapshot()))

    @app.post("/webhook/tradingview")
    async def tradingview(request: Request):
        if not webhook_ready:
            raise HTTPException(503, "Webhook deshabilitado: configura server.webhook_passphrase en config.yaml")
        try:
            data = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "El mensaje de la alerta debe ser JSON válido") from None
        if not hmac.compare_digest(
            str(data.get("passphrase", "")).encode("utf-8"),
            expected_passphrase.encode("utf-8"),
        ):
            raise HTTPException(401, "passphrase incorrecta")
        side = str(data.get("side", "")).lower()
        if side not in ("long", "short", "flat") or not data.get("symbol"):
            raise HTTPException(400, "Se requieren 'symbol' y 'side' (long | short | flat)")
        price = data.get("price")
        alert = TradingViewAlert(
            symbol=str(data["symbol"]),
            side=side,
            setup=str(data.get("setup", "tradingview")),
            price=float(price) if price not in (None, "") else None,
            timeframe=data.get("timeframe"),
            ts=int(time.time() * 1000),
        )
        engine.on_tradingview(alert)
        log.info("Alerta de TradingView: %s %s (%s)", alert.symbol, side, alert.setup)
        return {"ok": True, "symbol": alert.symbol, "side": side}

    return app
