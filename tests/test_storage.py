from quantflow.models import Signal
from quantflow.storage import Journal


def _signal() -> Signal:
    return Signal(
        symbol="BTCUSDT", ts=1_700_000_000_000, setup="absorcion", side="long",
        entry=100.0, stop=95.0, target=110.0, rr=2.0, probability=0.6,
        probability_model=0.55, expected_value_r=0.2, accepted=True,
        features={"orderflow": 0.5}, reasons=["absorción"], rejections=[],
    )


def _trade() -> dict:
    return dict(
        symbol="BTCUSDT", setup="absorcion", side="long", opened_ts=1, closed_ts=2,
        entry=100.0, exit=110.0, stop=95.0, target=110.0, qty=1.0, pnl=10.0,
        fees=0.1, r_multiple=1.9, probability=0.6, exit_reason="objetivo",
    )


def test_journal_starts_empty():
    journal = Journal()
    assert journal.load_calibration() == {}
    assert journal.db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    assert journal.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_log_signal_persists_all_fields():
    journal = Journal()
    journal.log_signal(_signal())
    row = journal.db.execute(
        "SELECT ts, symbol, setup, side, entry, stop, target, rr, probability, probability_model,"
        " expected_value_r, accepted, features, reasons, rejections FROM signals"
    ).fetchone()
    assert row[0] == 1_700_000_000_000
    assert row[1] == "BTCUSDT"
    assert row[11] == 1
    assert '"orderflow": 0.5' in row[12]
    assert "absorción" in row[13]


def test_log_trade_persists_all_fields():
    journal = Journal()
    journal.log_trade(_trade())
    row = journal.db.execute(
        "SELECT symbol, setup, side, entry, exit, pnl, exit_reason FROM trades"
    ).fetchone()
    assert row == ("BTCUSDT", "absorcion", "long", 100.0, 110.0, 10.0, "objetivo")


def test_calibration_roundtrip_and_upsert():
    journal = Journal()
    journal.save_calibration({"absorcion": {"n": 2, "wins": 1, "sum_pred": 1.1}})
    assert journal.load_calibration() == {"absorcion": {"n": 2, "wins": 1, "sum_pred": 1.1}}

    journal.save_calibration({"absorcion": {"n": 5, "wins": 3, "sum_pred": 2.75}})
    assert journal.load_calibration() == {"absorcion": {"n": 5, "wins": 3, "sum_pred": 2.75}}


def test_journal_creates_parent_directories(tmp_path):
    path = tmp_path / "anidado" / "journal.db"
    journal = Journal(path)
    journal.log_trade(_trade())
    assert path.exists()
    assert journal.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
