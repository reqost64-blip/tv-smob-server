import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot
from server.native_trade_notifications import format_clean_trade_message


def test_send_telegram_message_has_no_implicit_reply_keyboard(monkeypatch=None):
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        body = request.data.decode("utf-8")
        captured["body"] = body
        return DummyResponse()

    old_token = bot.config.TELEGRAM_BOT_TOKEN
    old_chat = bot.config.TELEGRAM_ADMIN_CHAT_ID
    old_urlopen = bot.urllib.request.urlopen
    try:
        bot.config.TELEGRAM_BOT_TOKEN = "test-token"
        bot.config.TELEGRAM_ADMIN_CHAT_ID = "123"
        bot.urllib.request.urlopen = fake_urlopen
        assert bot.send_telegram_message("smoke") is True
    finally:
        bot.config.TELEGRAM_BOT_TOKEN = old_token
        bot.config.TELEGRAM_ADMIN_CHAT_ID = old_chat
        bot.urllib.request.urlopen = old_urlopen
    assert "reply_markup" not in captured["body"]


def test_clean_trade_formatter_returns_text_only():
    text = format_clean_trade_message(
        {
            "event_type": "open",
            "symbol": "DJ30",
            "side": "BUY",
            "entry": 49620.40,
            "sl": 49510.00,
            "tp1": 49696.55,
            "tp2": 49912.30,
            "tp3": 50120.00,
            "risk_r": 1.0,
            "volume": 1.0,
        }
    )
    assert isinstance(text, str)
    assert "СДЕЛКА ОТКРЫТА" in text
    for keyboard_word in ("reply_markup", "keyboard", "⚡ Сигналы", "🛡 Риск"):
        assert keyboard_word not in text


if __name__ == "__main__":
    test_send_telegram_message_has_no_implicit_reply_keyboard()
    test_clean_trade_formatter_returns_text_only()
    print("ok")
