import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


DISABLED_CALLBACKS = [
    "refresh_signals",
    "signals_period:day",
    "signals_valid",
    "signals_watch",
    "signals_rejected",
    "signals_risky",
    "signal_accuracy",
    "signal_details:test",
    "refresh_sources",
    "source_details:test",
    "refresh_stats",
    "stats_period:day",
    "refresh_risk",
    "risk_period:day",
    "refresh_storage",
    "refresh_lab",
    "bias_accuracy",
    "bias_calibration",
]


def test_removed_sections_are_disabled():
    for callback in DISABLED_CALLBACKS:
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert "отключён" in text, callback
        assert "keyboard" not in (markup or {}), callback
        labels = [button.get("text") for row in markup.get("inline_keyboard", []) for button in row]
        assert labels == ["🌐 Dashboard"], callback


def test_no_signal_buttons_on_active_screens():
    for callback in ("refresh_center", "refresh_bias", "refresh_trades"):
        _, markup = bot.render_menu_callback(callback, "smoke")
        raw = str(markup)
        for forbidden in ("⚡ Сигналы", "🛡 Риск", "🧠 Sources", "📊 Статистика", "signal_accuracy", "bias_accuracy"):
            assert forbidden not in raw, callback


if __name__ == "__main__":
    test_removed_sections_are_disabled()
    test_no_signal_buttons_on_active_screens()
    print("ok")
