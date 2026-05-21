import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("telegram_bias_live.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store
from server.database import init_db
from server.live_bias_engine import calculate_live_bias_report, format_live_bias_telegram_message, live_bias_send_decision
from server.telegram_bot import handle_command


def main():
    init_db()
    report = calculate_live_bias_report(allow_network=False)
    text = format_live_bias_telegram_message(report)
    assert "LIVE MARKET BIAS" in text, text
    assert "CONSOLIDATION" not in text, text
    assert "Risk:" in text and "Data Quality:" in text, text

    bias_store.save_live_bias_report(report, sent_to_telegram=False, send_reason="smoke")
    response = handle_command("/bias")
    assert "LIVE MARKET BIAS" in response, response
    assert "CONSOLIDATION" not in response, response

    should_send, reason = live_bias_send_decision(report, bias_store.latest_live_bias(), force_send=False)
    assert should_send is False, reason
    forced, forced_reason = live_bias_send_decision(report, bias_store.latest_live_bias(), force_send=True)
    assert forced is True and forced_reason == "force_send", forced_reason
    print({"telegram_bias_live": "ok"})


if __name__ == "__main__":
    main()
