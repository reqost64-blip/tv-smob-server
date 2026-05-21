import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("signal_store.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import signal_store
from server.database import init_db
from server.signal_parser import parse_tradingview_signal
from server.signal_scoring import score_signal


def main():
    init_db()
    signal = score_signal(
        parse_tradingview_signal(
            {
                "source": "TradingView Scanner",
                "symbol": "NAS100",
                "direction": "LONG",
                "entry": 100,
                "sl": 95,
                "tp1": 108,
                "setup": "VWAP_RECLAIM",
            }
        )
    )
    signal_store.save_signal(signal)
    assert signal_store.is_duplicate(signal["dedupe_key"]) is True
    latest = signal_store.latest_signals()
    assert len(latest) == 1, latest
    assert "raw_text" not in latest[0], latest[0]
    assert "raw_text_preview" not in latest[0], latest[0]
    signal_store.save_signal_evaluations(signal["signal_id"], [{"horizon": "15m", "result": "correct", "move_pct": 0.2, "r_multiple": 0.5}])
    sources = signal_store.source_reliability()
    assert sources and sources[0]["total_signals"] == 1, sources
    assert sources[0]["evaluated_count"] >= 1, sources
    print({"signal_store": "ok"})


if __name__ == "__main__":
    main()
