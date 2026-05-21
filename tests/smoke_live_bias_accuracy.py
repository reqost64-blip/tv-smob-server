import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("live_bias_accuracy.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store
from server.database import init_db
from server.live_bias_accuracy import live_bias_accuracy


def row(symbol, direction, confidence, price, risk="LOW", factor=35):
    return {
        "symbol": symbol,
        "direction": direction,
        "bias": direction,
        "confidence": confidence,
        "long_probability": confidence if direction == "LONG" else 100 - confidence,
        "short_probability": confidence if direction == "SHORT" else 100 - confidence,
        "final_score": factor if direction == "LONG" else -factor,
        "strength": "MEDIUM",
        "risk": risk,
        "data_quality_score": 80,
        "current_price": price,
        "factor_scores": {"trend": factor, "momentum": factor, "vwap": factor},
        "source_availability": {"market_data": "available"},
        "reasons": ["smoke"],
        "risk_flags": [],
    }


def save_snapshot(ts, rows):
    bias_store.save_live_bias_report({"timestamp": ts.isoformat(), "symbols": rows}, sent_to_telegram=False, send_reason="smoke")


def main():
    init_db()
    start = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    for idx in range(10):
        ts = start + timedelta(minutes=30 * idx)
        save_snapshot(
            ts,
            [
                row("NAS100", "LONG", 63, 100 + idx),
                row("SP500", "SHORT", 61, 200 - idx),
                row("DJ30", "LONG", 54, 300 + (0.01 if idx % 2 else 0)),
                row("XAUUSD", "LONG", 66, 400 - idx),
            ],
        )

    result = live_bias_accuracy(limit=1000)
    assert result["snapshot_count"] == 40, result
    assert result["overall"]["30m"]["accuracy"] is not None, result
    assert result["by_symbol"]["NAS100"]["30m"]["accuracy"] == 100.0, result["by_symbol"]["NAS100"]
    assert result["by_symbol"]["SP500"]["30m"]["accuracy"] == 100.0, result["by_symbol"]["SP500"]
    assert result["by_symbol"]["XAUUSD"]["30m"]["accuracy"] == 0.0, result["by_symbol"]["XAUUSD"]
    assert result["by_symbol"]["DJ30"]["30m"]["neutral"] > 0, result["by_symbol"]["DJ30"]
    assert "61-65" in result["by_confidence_bucket"], result["by_confidence_bucket"]
    assert "trend" in result["factor_stats"], result["factor_stats"]
    print({"live_bias_accuracy": "ok", "evaluated": result["evaluated_count"]})


if __name__ == "__main__":
    main()
