import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("signal_accuracy.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store, signal_store
from server.database import init_db
from server.signal_accuracy import evaluate_signal_accuracy
from server.signal_parser import parse_tradingview_signal
from server.signal_scoring import score_signal


def save_price(ts, price):
    bias_store.save_live_bias_report(
        {
            "timestamp": ts.isoformat(),
            "symbols": [
                {
                    "symbol": "NAS100",
                    "direction": "LONG",
                    "bias": "LONG",
                    "confidence": 60,
                    "long_probability": 60,
                    "short_probability": 40,
                    "final_score": 20,
                    "strength": "MEDIUM",
                    "risk": "LOW",
                    "data_quality_score": 80,
                    "current_price": price,
                    "factor_scores": {},
                    "source_availability": {},
                    "reasons": [],
                    "risk_flags": [],
                }
            ],
        }
    )


def main():
    init_db()
    start = datetime.now(timezone.utc) - timedelta(minutes=40)
    signal = parse_tradingview_signal({"source": "TradingView Scanner", "symbol": "NAS100", "direction": "LONG", "entry": 100, "sl": 95, "tp1": 103, "timestamp": start.isoformat()})
    scored = score_signal(signal)
    scored["verdict"] = "VALID_SIGNAL"
    scored["status"] = "validated"
    signal_store.save_signal(scored)
    save_price(start + timedelta(minutes=10), 101)
    save_price(start + timedelta(minutes=20), 103.5)
    result = evaluate_signal_accuracy(limit=100)
    assert result["evaluated_count"] > 0, result
    assert result["overall"]["all"]["correct"] >= 1, result
    print({"signal_accuracy": "ok", "evaluated": result["evaluated_count"]})


if __name__ == "__main__":
    main()
