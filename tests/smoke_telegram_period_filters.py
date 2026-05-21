import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_trade_and_stats_period_callbacks():
    for prefix, title in [("trades", "СДЕЛКИ"), ("stats", "СТАТИСТИКА")]:
        for period, label in [("day", "СЕГОДНЯ"), ("week", "НЕДЕЛЯ"), ("month", "МЕСЯЦ"), ("all", "ВСЁ ВРЕМЯ")]:
            text, markup = bot.render_menu_callback(f"{prefix}_period:{period}", "smoke")
            assert title in text
            assert label in text
            assert "keyboard" not in (markup or {})
            assert f"{prefix}_period:{period}" in str(markup)


def test_store_period_mapping():
    assert bot.period_to_store("day") == "today"
    assert bot.period_to_store("week") == "7d"
    assert bot.period_to_store("month") == "30d"
    assert bot.period_to_store("all") == "all"


if __name__ == "__main__":
    test_trade_and_stats_period_callbacks()
    test_store_period_mapping()
    print("ok")
