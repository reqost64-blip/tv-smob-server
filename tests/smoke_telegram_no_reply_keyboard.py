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
    assert is_reply(bot.dashboard_keyboard())
    assert "🌍 Рынок" not in str(bot.dashboard_keyboard())
    assert "Действия" not in str(bot.dashboard_keyboard())
    assert "🏆 Рекорды" not in str(bot.dashboard_keyboard())
    assert "🤖 Боты" not in str(bot.dashboard_keyboard())


def test_regular_screens_do_not_return_reply_keyboard():
    callbacks = [
        "refresh_center",
        "refresh_bias",
        "refresh_signals",
        "refresh_trades",
        "refresh_stats",
        "refresh_risk",
        "refresh_sources",
        "refresh_storage",
        "refresh_lab",
        "trades_period:day",
        "stats_period:week",
    ]
    for callback in callbacks:
        text, markup = bot.render_menu_callback(callback, "smoke")
        assert text
        assert not is_reply(markup), callback
        assert is_inline(markup), callback


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
    test_send_message_has_no_default_reply_markup()
    print("ok")
