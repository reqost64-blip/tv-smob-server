import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_lab_screen_disabled_in_telegram():
    text, markup = bot.render_menu_callback("refresh_lab", "smoke")
    assert "отключён" in text
    assert "keyboard" not in (markup or {})
    assert "Dashboard" in str(markup)


if __name__ == "__main__":
    test_lab_screen_disabled_in_telegram()
    print("ok")
