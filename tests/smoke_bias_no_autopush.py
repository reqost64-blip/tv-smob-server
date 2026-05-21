import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("bias_no_autopush.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import main
from server.database import init_db


def fake_bias_report():
    return {
        "report_date": "2026-05-22",
        "run_at": "2026-05-22T09:20:00+00:00",
        "ny_time": "09:20 NY",
        "berlin_time": "15:20 DE",
        "macro_risk": "UNKNOWN",
        "data_quality_score": 0,
        "symbols": [],
        "telegram_text": "must not be sent automatically",
    }


def fake_live_bias_report():
    return {
        "timestamp": "2026-05-22T09:20:00+00:00",
        "symbols": [
            {
                "symbol": "NAS100",
                "direction": "LONG",
                "confidence": 70,
                "long_probability": 70,
                "short_probability": 30,
                "final_score": 40,
                "strength": "STRONG",
                "risk": "LOW",
                "data_quality_score": 80,
                "factor_scores": {},
                "source_availability": {},
                "reasons": ["smoke"],
            }
        ],
    }


def main_test():
    init_db()

    def fail_send(*_args, **_kwargs):
        raise AssertionError("bias auto-push attempted")

    original_send = main.send_telegram_message
    original_bias = main.calculate_bias_report
    original_live = main.calculate_live_bias_report
    try:
        main.send_telegram_message = fail_send
        main.calculate_bias_report = lambda allow_network=True: fake_bias_report()
        sent, reason, _ = main.send_bias_report_if_due(force=True)
        assert sent is False, (sent, reason)
        assert reason == "auto_bias_telegram_disabled", reason

        main.calculate_live_bias_report = lambda allow_network=True, symbol=None: fake_live_bias_report()
        report, live_sent, live_reason = main.run_live_bias_cycle(allow_network=False, send=False, force_send=True)
        assert report["symbols"][0]["symbol"] == "NAS100", report
        assert live_sent is False, (live_sent, live_reason)
        assert live_reason == "send_disabled", live_reason
    finally:
        main.send_telegram_message = original_send
        main.calculate_bias_report = original_bias
        main.calculate_live_bias_report = original_live

    print({"bias_no_autopush": "ok"})


if __name__ == "__main__":
    main_test()
