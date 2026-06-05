import json
import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("DB_FILE", "tests/smoke_control_center.sqlite3")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot


def _flatten_keyboard_texts(markup):
    return [
        button.get("text") if isinstance(button, dict) else button
        for row in markup.get("keyboard", [])
        for button in row
    ]


def _keyboard_text_rows(markup):
    return [
        [button.get("text") if isinstance(button, dict) else button for button in row]
        for row in markup.get("keyboard", [])
    ]


def test_main_keyboard_has_exact_rows():
    assert _keyboard_text_rows(telegram_bot.dashboard_keyboard()) == [
        ["📊 Статус", "🧾 Сделки"],
        ["📈 Bias", "🌐 Сайт"],
    ]


def test_removed_buttons_not_in_main_keyboard():
    labels = set(_flatten_keyboard_texts(telegram_bot.dashboard_keyboard()))
    for old in ("🎛 Пульт", "⚡ Сигналы", "📊 Статистика", "🛡 Риск", "🧠 Sources", "🌍 Рынок", "Действия", "🏆 Рекорды", "🤖 Боты", "🌐 Открыть SMOB"):
        assert old not in labels


def test_site_inline_keyboard_uses_dashboard_url():
    markup = telegram_bot.site_inline_keyboard()
    button = markup["inline_keyboard"][0][0]
    assert button["url"] == telegram_bot.DASHBOARD_URL


def test_site_aliases_and_commands():
    assert telegram_bot.normalize_dashboard_button("🌐 Сайт") == "/dashboard"
    assert telegram_bot.normalize_dashboard_button("Сайт") == "/dashboard"
    assert telegram_bot.normalize_dashboard_button("📈 Bias") == "/bias"
    assert telegram_bot.normalize_dashboard_button("📊 Статус") == "/status"
    assert telegram_bot.normalize_dashboard_button("🧾 Сделки") == "/trades"
    assert telegram_bot.handle_command("/dashboard").startswith("🌐 Dashboard")


def test_site_response_does_not_leak_secret():
    serialized = json.dumps(
        {
            "message": telegram_bot.site_message(),
            "keyboard": telegram_bot.site_inline_keyboard(),
        },
        ensure_ascii=False,
    )
    assert "do-not-leak-smoke-secret" not in serialized


if __name__ == "__main__":
    test_main_keyboard_has_exact_rows()
    test_removed_buttons_not_in_main_keyboard()
    test_site_inline_keyboard_uses_dashboard_url()
    test_site_aliases_and_commands()
    test_site_response_does_not_leak_secret()
    print({"telegram_site": "ok", "url": telegram_bot.DASHBOARD_URL})
