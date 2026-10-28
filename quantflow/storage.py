"""Diario de señales y operaciones en SQLite, más el estado de calibración."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER, symbol TEXT, setup TEXT, side TEXT,
    entry REAL, stop REAL, target REAL, rr REAL,
    probability REAL, probability_model REAL, expected_value_r REAL,
    accepted INTEGER, features TEXT, reasons TEXT, rejections TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, setup TEXT, side TEXT,
    opened_ts INTEGER, closed_ts INTEGER,
    entry REAL, exit REAL, stop REAL, target REAL, qty REAL,
    pnl REAL, fees REAL, r_multiple REAL, probability REAL, exit_reason TEXT
);
CREATE TABLE IF NOT EXISTS calibration (
    setup TEXT PRIMARY KEY, n INTEGER, wins INTEGER, sum_pred REAL
);
"""


class Journal:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)

    def log_signal(self, s: Signal) -> None:
        self.db.execute(
            "INSERT INTO signals (ts, symbol, setup, side, entry, stop, target, rr, probability,"
            " probability_model, expected_value_r, accepted, features, reasons, rejections)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.ts, s.symbol, s.setup, s.side, s.entry, s.stop, s.target, s.rr, s.probability,
             s.probability_model, s.expected_value_r, int(s.accepted), json.dumps(s.features),
             json.dumps(s.reasons, ensure_ascii=False), json.dumps(s.rejections, ensure_ascii=False)),
        )
        self.db.commit()

    def log_trade(self, t: dict) -> None:
        cols = ["symbol", "setup", "side", "opened_ts", "closed_ts", "entry", "exit", "stop", "target",
                "qty", "pnl", "fees", "r_multiple", "probability", "exit_reason"]
        self.db.execute(
            f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [t[c] for c in cols],
        )
        self.db.commit()

    def load_calibration(self) -> dict:
        rows = self.db.execute("SELECT setup, n, wins, sum_pred FROM calibration").fetchall()
        return {r[0]: {"n": r[1], "wins": r[2], "sum_pred": r[3]} for r in rows}

    def save_calibration(self, stats: dict) -> None:
        self.db.executemany(
            "INSERT INTO calibration (setup, n, wins, sum_pred) VALUES (?,?,?,?)"
            " ON CONFLICT(setup) DO UPDATE SET n=excluded.n, wins=excluded.wins, sum_pred=excluded.sum_pred",
            [(k, v["n"], v["wins"], v["sum_pred"]) for k, v in stats.items()],
        )
        self.db.commit()
