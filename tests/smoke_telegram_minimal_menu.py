import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def labels(markup):
    return [button.get("text") for row in markup.get("inline_keyboard", []) for button in row]


def test_start_menu_text_and_site_url():
    text = bot.handle_command("/start")
    assert "MT5 МЕНЮ" in text
    assert bot.handle_command("/dashboard").startswith("🌐 Dashboard")
    site_markup = bot.site_inline_keyboard()
    assert site_markup["inline_keyboard"][0][0]["url"] == bot.DASHBOARD_URL


def test_bias_inline_minimal():
    _, markup = bot.render_menu_callback("refresh_bias", "smoke")
    assert "keyboard" not in markup
    assert labels(markup) == ["🔄 Обновить", "🌐 Dashboard"]


def test_trades_inline_minimal_with_periods():
    _, markup = bot.render_menu_callback("refresh_trades", "smoke")
    raw = str(markup)
    for callback in ("trades_period:day", "trades_period:week", "trades_period:month", "trades_period:all"):
        assert callback in raw
    for forbidden in ("refresh_signals", "refresh_stats", "refresh_risk", "refresh_sources", "signal_accuracy"):
        assert forbidden not in raw
    assert labels(markup)[-2:] == ["🔄 Обновить", "🌐 Dashboard"]


def test_old_reply_texts_do_not_route_to_extra_screens():
    for text in ("🎛 Пульт", "⚡ Сигналы", "📊 Статистика", "🛡 Риск", "🧠 Sources", "📉 Аналитика"):
        assert bot.normalize_dashboard_button(text) == text


if __name__ == "__main__":
    test_start_menu_text_and_site_url()
    test_bias_inline_minimal()
    test_trades_inline_minimal_with_periods()
    test_old_reply_texts_do_not_route_to_extra_screens()
    print("ok")
