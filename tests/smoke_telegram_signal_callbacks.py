import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("telegram_signal_callbacks.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.telegram_bot import render_menu_callback


def main():
    callbacks = [
        "refresh_center",
        "refresh_bias",
        "refresh_signals",
        "refresh_processed_trades",
        "refresh_stats",
        "refresh_risk",
        "refresh_sources",
        "bias_accuracy",
        "signal_accuracy",
    ]
    for callback in callbacks:
        text, keyboard = render_menu_callback(callback, "smoke-chat")
        assert text and isinstance(keyboard, dict), callback
    print({"telegram_signal_callbacks": "ok", "callbacks": len(callbacks)})


if __name__ == "__main__":
    main()
