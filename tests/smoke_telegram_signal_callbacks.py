import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("telegram_signal_callbacks.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import init_db
from server.telegram_bot import render_menu_callback


def main():
    init_db()
    active_callbacks = ["refresh_center", "refresh_bias", "refresh_processed_trades", "trades_period:day"]
    disabled_callbacks = [
        "refresh_signals",
        "refresh_analytics",
        "refresh_stats",
        "refresh_risk",
        "refresh_sources",
        "bias_accuracy",
        "signal_accuracy",
        "signal_details:test",
        "source_details:test",
    ]
    for callback in active_callbacks:
        text, keyboard = render_menu_callback(callback, "smoke-chat")
        assert text and isinstance(keyboard, dict), callback
        assert "keyboard" not in keyboard, callback
        assert "inline_keyboard" in keyboard, callback
    for callback in disabled_callbacks:
        text, keyboard = render_menu_callback(callback, "smoke-chat")
        assert "отключён" in text, callback
        assert "keyboard" not in keyboard, callback
        assert str(keyboard).count("Dashboard") == 1, callback
    print({"telegram_signal_callbacks": "ok", "active": len(active_callbacks), "disabled": len(disabled_callbacks)})


if __name__ == "__main__":
    main()
