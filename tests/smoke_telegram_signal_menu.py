import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_signal_menu_disabled_in_telegram():
    text, markup = bot.render_menu_callback("refresh_signals", "smoke")
    assert "отключён" in text
    assert "keyboard" not in (markup or {})
    assert "signals_period:day" not in str(markup)
    assert str(markup).count("Dashboard") == 1


if __name__ == "__main__":
    test_signal_menu_disabled_in_telegram()
    print("ok")
