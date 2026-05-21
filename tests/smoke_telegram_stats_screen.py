import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_stats_default_today():
    text, markup = bot.render_menu_callback("refresh_stats", "smoke")
    assert "СТАТИСТИКА · СЕГОДНЯ" in text
    assert "keyboard" not in (markup or {})
    raw = str(markup)
    assert "stats_period:day" in raw
    assert "stats_period:week" in raw
    assert "stats_period:month" in raw
    assert "stats_period:all" in raw


if __name__ == "__main__":
    test_stats_default_today()
    print("ok")
