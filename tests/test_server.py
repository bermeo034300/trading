import pytest
from fastapi.testclient import TestClient

from quantflow.config import ROOT, load_config
from quantflow.engine import Engine
from quantflow.server import create_app, webhook_enabled
from quantflow.storage import Journal

PASSPHRASE = "passphrase-de-prueba"


def make_cfg(passphrase: str = PASSPHRASE):
    cfg = load_config(ROOT / "config.yaml")
    cfg["server"]["webhook_passphrase"] = passphrase
    return cfg


def make_client(cfg) -> TestClient:
    engine = Engine(cfg, journal=Journal())
    return TestClient(create_app(cfg, engine=engine, start_feed=False))


def test_dashboard_and_state_are_served():
    with make_client(make_cfg()) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert len(r.text) > 0

        r = client.get("/api/state")
        assert r.status_code == 200
        data = r.json()
        assert set(data) == {
            "ts", "trading_enabled", "symbols", "stats", "trades", "equity_curve", "calibration",
        }
        assert data["trading_enabled"] is True
        assert data["symbols"] == {}


def test_webhook_accepts_valid_alert_and_normalizes_symbol():
    with make_client(make_cfg()) as client:
        r = client.post(
            "/webhook/tradingview",
            json={"passphrase": PASSPHRASE, "symbol": "BINANCE:BTCUSDT.P", "side": "long", "price": 100.5},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "symbol": "BTCUSDT", "side": "long"}

        state = client.get("/api/state").json()
        assert state["symbols"]["BTCUSDT"]["tradingview"]["side"] == "long"


def test_webhook_accepts_flat_side():
    with make_client(make_cfg()) as client:
        r = client.post(
            "/webhook/tradingview",
            json={"passphrase": PASSPHRASE, "symbol": "BTCUSDT", "side": "flat"},
        )
        assert r.status_code == 200


def test_webhook_rejects_wrong_passphrase():
    with make_client(make_cfg()) as client:
        r = client.post("/webhook/tradingview", json={"passphrase": "otra", "symbol": "BTCUSDT", "side": "long"})
        assert r.status_code == 401


def test_webhook_rejects_invalid_json():
    with make_client(make_cfg()) as client:
        r = client.post("/webhook/tradingview", content="esto no es json")
        assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "BTCUSDT", "side": "lateral"},
        {"symbol": "BTCUSDT"},
        {"side": "long"},
    ],
)
def test_webhook_rejects_incomplete_or_invalid_payload(payload):
    with make_client(make_cfg()) as client:
        r = client.post("/webhook/tradingview", json={"passphrase": PASSPHRASE, **payload})
        assert r.status_code == 400


def test_webhook_is_disabled_with_default_passphrase():
    cfg = load_config(ROOT / "config.yaml")
    assert webhook_enabled(cfg) is False
    with make_client(cfg) as client:
        r = client.post(
            "/webhook/tradingview",
            json={"passphrase": "cambia-esto", "symbol": "BTCUSDT", "side": "long"},
        )
        assert r.status_code == 503


def test_webhook_enabled_only_with_real_passphrase():
    assert webhook_enabled(make_cfg()) is True
    assert webhook_enabled(make_cfg("")) is False
    assert webhook_enabled(make_cfg("   ")) is False


@pytest.mark.parametrize("padded", ["cambia-esto ", " cambia-esto", "cambia-esto\n"])
def test_webhook_treats_padded_default_passphrase_as_disabled(padded):
    cfg = make_cfg(padded)
    assert webhook_enabled(cfg) is False
    with make_client(cfg) as client:
        r = client.post(
            "/webhook/tradingview",
            json={"passphrase": padded, "symbol": "BTCUSDT", "side": "long"},
        )
        assert r.status_code == 503


def test_webhook_disabled_wins_over_invalid_json():
    with make_client(load_config(ROOT / "config.yaml")) as client:
        assert client.post("/webhook/tradingview", content="no json").status_code == 503


def test_webhook_supports_utf8_passphrase():
    passphrase = "contraseña-ñ-🔥"
    with make_client(make_cfg(passphrase)) as client:
        payload = {"passphrase": passphrase, "symbol": "BTCUSDT", "side": "long"}
        assert client.post("/webhook/tradingview", json=payload).status_code == 200


def test_webhook_normalizes_padded_passphrase():
    with make_client(make_cfg("  secreto  ")) as client:
        payload = {"passphrase": "secreto", "symbol": "BTCUSDT", "side": "long"}
        assert client.post("/webhook/tradingview", json=payload).status_code == 200
