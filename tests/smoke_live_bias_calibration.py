import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("live_bias_calibration.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store
from server.database import init_db
from server.live_bias_accuracy import live_bias_calibration


def live_row(symbol, direction, price, factor_scores, confidence=64, risk="LOW"):
    return {
        "symbol": symbol,
        "direction": direction,
        "bias": direction,
        "confidence": confidence,
        "long_probability": confidence if direction == "LONG" else 100 - confidence,
        "short_probability": confidence if direction == "SHORT" else 100 - confidence,
        "final_score": 30 if direction == "LONG" else -30,
        "strength": "MEDIUM",
        "risk": risk,
        "data_quality_score": 82,
        "current_price": price,
        "factor_scores": factor_scores,
        "source_availability": {"market_data": "available"},
        "reasons": ["smoke"],
        "risk_flags": [],
    }


def main():
    init_db()
    start = datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc)
    for idx in range(12):
        ts = start + timedelta(minutes=30 * idx)
        bias_store.save_live_bias_report(
            {
                "timestamp": ts.isoformat(),
                "symbols": [
                    live_row("NAS100", "LONG", 100 + idx, {"trend": 40, "momentum": 35, "vwap": 30}),
                    live_row("XAUUSD", "LONG", 200 - idx, {"trend": 35, "momentum": -35, "vwap": 20}, confidence=66, risk="MEDIUM"),
                ],
            },
            sent_to_telegram=False,
            send_reason="smoke",
        )

    result = live_bias_calibration(limit=1000)
    assert result["requires_human_approval"] is True, result
    assert result["best_symbol"], result
    assert result["worst_symbol"], result
    assert result["recommended_weight_adjustments"], result
    assert any(item["type"] in {"weight_increase_candidate", "weight_decrease_candidate", "collect_more_data"} for item in result["recommended_weight_adjustments"]), result
    assert "note" in result and "No weights are changed" in result["note"], result
    print({"live_bias_calibration": "ok", "suggestions": len(result["recommended_weight_adjustments"])})


if __name__ == "__main__":
    main()
