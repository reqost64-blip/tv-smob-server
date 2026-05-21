import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).with_name("signal_scoring.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store
from server.database import init_db
from server.signal_parser import parse_tradingview_signal
from server.signal_scoring import score_signal


def save_bias(direction="LONG", confidence=70, risk="LOW"):
    bias_store.save_live_bias_report(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbols": [
                {
                    "symbol": "NAS100",
                    "direction": direction,
                    "bias": direction,
                    "confidence": confidence,
                    "long_probability": confidence if direction == "LONG" else 100 - confidence,
                    "short_probability": confidence if direction == "SHORT" else 100 - confidence,
                    "final_score": 30,
                    "strength": "MEDIUM",
                    "risk": risk,
                    "data_quality_score": 80,
                    "factor_scores": {},
                    "source_availability": {},
                    "reasons": [],
                    "risk_flags": [],
                }
            ],
        }
    )


def signal(**extra):
    payload = {
        "source": "TradingView Scanner",
        "symbol": "NAS100",
        "direction": "LONG",
        "setup": "VWAP_RECLAIM",
        "entry": 100,
        "sl": 95,
        "tp1": 108,
        "timeframe": "5m",
        "confirmations": ["Price above VWAP", "M5 momentum up"],
    }
    payload.update(extra)
    return parse_tradingview_signal(payload)


def main():
    init_db()
    save_bias("LONG", 72, "LOW")
    scored = score_signal(signal())
    assert scored["score"] >= 70, scored
    assert scored["verdict"] == "VALID_SIGNAL", scored

    save_bias("SHORT", 72, "LOW")
    conflict = score_signal(signal())
    assert conflict["score"] < scored["score"], conflict

    low_rr = score_signal(signal(tp1=101))
    assert low_rr["verdict"] == "REJECTED", low_rr
    assert any("RR" in item for item in low_rr["rejection_reasons"]), low_rr

    old = signal(timestamp=(datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat())
    expired = score_signal(old)
    assert expired["verdict"] == "EXPIRED", expired
    duplicate = score_signal(signal(), duplicate=True)
    assert duplicate["verdict"] == "DUPLICATE", duplicate
    print({"signal_scoring": "ok"})


if __name__ == "__main__":
    main()
