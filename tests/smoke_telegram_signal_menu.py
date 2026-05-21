import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_signal_menu_period_buttons():
    text, markup = bot.render_menu_callback("refresh_signals", "smoke")
    assert "СИГНАЛЫ" in text
    raw = str(markup)
    for label in ["Сегодня", "Неделя", "Месяц", "Всё время"]:
        assert label in raw
    assert "signals_period:day" in raw


if __name__ == "__main__":
    test_signal_menu_period_buttons()
    print("ok")
