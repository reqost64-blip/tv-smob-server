import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("telegram_signal_notifications.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.telegram_bot import format_signal_notification, format_signal_result_notification


SIGNAL = {
    "symbol": "NAS100",
    "direction": "LONG",
    "score": 72,
    "setup": "VWAP reclaim",
    "entry_zone_low": 18842,
    "entry_zone_high": 18848,
    "sl": 18828,
    "tp1": 18862,
    "tp2": 18884,
    "risk_level": "MEDIUM",
    "expiry_minutes": 20,
    "reasons": ["Live Bias LONG 63%", "Price above VWAP", "M5 momentum up", "RR acceptable"],
    "source_name": "TradingView Scanner",
    "verdict": "VALID_SIGNAL",
}


def main():
    valid = format_signal_notification(SIGNAL)
    assert "SCALP SIGNAL" in valid and "NAS100" in valid, valid
    watch = format_signal_notification({**SIGNAL, "verdict": "WAIT_CONFIRMATION", "score": 61})
    assert "SIGNAL WATCH" in watch, watch
    result = format_signal_result_notification(SIGNAL, {"horizon": "30m", "result": "correct", "r_multiple": 0.42}, {"trust_score": 76})
    assert "SIGNAL RESULT" in result and "CORRECT" in result, result
    print({"telegram_signal_notifications": "ok"})


if __name__ == "__main__":
    main()
