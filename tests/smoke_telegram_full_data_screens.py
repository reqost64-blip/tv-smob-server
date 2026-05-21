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


def test_core_screens():
    assert_screen("refresh_center", ["ЦЕНТР УПРАВЛЕНИЯ", "Счёт:", "Торговля:", "Сигналы:", "Риск:"])
    assert_screen("refresh_bias", ["ЖИВОЙ BIAS"])
    assert_screen("refresh_signals", ["СИГНАЛЫ", "СЕГОДНЯ"])
    assert_screen("refresh_trades", ["СДЕЛКИ", "СЕГОДНЯ"])
    assert_screen("refresh_stats", ["СТАТИСТИКА", "СЕГОДНЯ"])
    assert_screen("refresh_risk", ["КОНТРОЛЬ РИСКА", "СЕГОДНЯ"])
    assert_screen("refresh_sources", ["ИСТОЧНИКИ СИГНАЛОВ"])


if __name__ == "__main__":
    test_core_screens()
    print("ok")
