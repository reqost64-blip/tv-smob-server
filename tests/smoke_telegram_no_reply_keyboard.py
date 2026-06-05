import os
import sys
import urllib.parse
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import config
from server import telegram_bot as bot


def is_reply(markup):
    return isinstance(markup, dict) and "keyboard" in markup


def is_inline(markup):
    return isinstance(markup, dict) and "inline_keyboard" in markup


def test_menu_only_reply_keyboard():
    keyboard = bot.dashboard_keyboard()
    assert is_reply(keyboard)
    assert keyboard["keyboard"] == [
        [{"text": "📊 Статус"}, {"text": "🧾 Сделки"}],
        [{"text": "📈 Bias"}, {"text": "🌐 Сайт", "web_app": {"url": bot.DASHBOARD_URL}}],
    ]
    for old in ("🎛 Пульт", "⚡ Сигналы", "📊 Статистика", "🛡 Риск", "🧠 Sources", "🌍 Рынок", "Действия", "🏆 Рекорды", "🤖 Боты"):
        assert old not in str(keyboard)


def test_regular_screens_do_not_return_reply_keyboard():
    callbacks = [
        "refresh_center",
        "refresh_bias",
        "refresh_trades",
        "trades_period:day",
        "trades_period:week",
        "trades_period:month",
        "trades_period:all",
    ]
    for callback in callbacks:
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert text
        assert not is_reply(markup), callback
        assert is_inline(markup), callback


def test_old_sections_disabled_without_reply_keyboard():
    callbacks = [
        "refresh_signals",
        "refresh_stats",
        "refresh_risk",
        "refresh_sources",
        "refresh_storage",
        "refresh_lab",
        "signal_accuracy",
        "source_details:old",
        "signal_details:old",
    ]
    for callback in callbacks:
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert "отключён" in text
        assert "keyboard" not in (markup or {})
        assert str(markup).count("Dashboard") == 1


def test_regular_command_handler_registered():
    assert bot.regular_command_handler is not None
    for command in ["/status", "/bias", "/trades"]:
        assert bot._command_to_callback(command), command
    for command in ["/signals", "/stats", "/risk", "/sources", "/storage", "/lab"]:
        assert not bot._command_to_callback(command), command
        assert "отключён" in bot.handle_command(command)


def test_send_message_has_no_default_reply_markup(monkeypatch=None):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data.decode("utf-8")
        return FakeResponse()

    old_token = config.TELEGRAM_BOT_TOKEN
    old_chat = config.TELEGRAM_ADMIN_CHAT_ID
    old_urlopen = bot.urllib.request.urlopen
    try:
        config.TELEGRAM_BOT_TOKEN = "test-token"
        config.TELEGRAM_ADMIN_CHAT_ID = "123"
        bot.urllib.request.urlopen = fake_urlopen
        assert bot.send_telegram_message("test") is True
    finally:
        config.TELEGRAM_BOT_TOKEN = old_token
        config.TELEGRAM_ADMIN_CHAT_ID = old_chat
        bot.urllib.request.urlopen = old_urlopen
    parsed = urllib.parse.parse_qs(captured["body"])
    assert "reply_markup" not in parsed


if __name__ == "__main__":
    test_menu_only_reply_keyboard()
    test_regular_screens_do_not_return_reply_keyboard()
    test_old_sections_disabled_without_reply_keyboard()
    test_regular_command_handler_registered()
    test_send_message_has_no_default_reply_markup()
    print("ok")
