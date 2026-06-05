import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def assert_screen(callback, required):
    text, markup = bot.render_menu_callback(callback, "smoke")
    assert "keyboard" not in (markup or {})
    for item in required:
        assert item in text, (callback, item, text)
    forbidden = ["Signal Board", "Risk Control", "Storage Health", "Signal Sources", "Signal Accuracy"]
    for item in forbidden:
        assert item not in text, (callback, item)


def test_minimal_active_screens():
    assert_screen("refresh_center", ["СТАТУС MT5", "Счёт:", "Торговля:"])
    assert_screen("refresh_bias", ["ЖИВОЙ BIAS"])
    assert_screen("refresh_trades", ["СДЕЛКИ", "СЕГОДНЯ"])


def test_removed_full_screens_disabled():
    for callback in ("refresh_signals", "refresh_stats", "refresh_risk", "refresh_sources", "refresh_storage", "refresh_lab"):
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert "отключён" in text
        assert "keyboard" not in (markup or {})


if __name__ == "__main__":
    test_minimal_active_screens()
    test_removed_full_screens_disabled()
    print("ok")
