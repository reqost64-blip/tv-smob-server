import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_trades_period_callbacks_only_active():
    periods = [
        ("day", "СЕГОДНЯ"),
        ("week", "НЕДЕЛЯ"),
        ("month", "МЕСЯЦ"),
        ("all", "ВСЁ ВРЕМЯ"),
    ]
    for period, label in periods:
        text, markup = bot.render_menu_callback(f"trades_period:{period}", "smoke")
        assert "СДЕЛКИ" in text
        assert label in text
        assert "keyboard" not in (markup or {})
        assert f"trades_period:{period}" in str(markup)


def test_disabled_period_callbacks():
    for callback in ("stats_period:day", "signals_period:week", "risk_period:all"):
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert "отключён" in text
        assert "keyboard" not in (markup or {})
        assert str(markup).count("Dashboard") == 1


def test_store_period_mapping():
    assert bot.period_to_store("day") == "today"
    assert bot.period_to_store("week") == "7d"
    assert bot.period_to_store("month") == "30d"
    assert bot.period_to_store("all") == "all"


if __name__ == "__main__":
    test_trades_period_callbacks_only_active()
    test_disabled_period_callbacks()
    test_store_period_mapping()
    print("ok")
