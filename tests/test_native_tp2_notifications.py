import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.native_trade_notifications import (
    accounting_event_type,
    format_clean_trade_message,
    normalizeNativeTradeEvent,
)


def test_tp2_silent_is_important_telegram_event():
    payload = {
        "event_type": "tp2_silent",
        "telegram_silent": True,
        "bot_id": "DJ30_ORB_VWAP_RSI_OF",
        "symbol": "DJ30",
        "side": "buy",
        "tp2": 50000,
        "profit": 92.38,
        "profit_r": 0,
        "closed_percent": 25,
        "remaining_percent": 0,
        "time": "2026-05-13T17:51:00+00:00",
    }

    normalized = normalizeNativeTradeEvent(payload)

    assert normalized["normalizedType"] == "trade_tp2"
    assert normalized["telegramTemplate"] == "tp2"
    assert normalized["shouldNotifyTelegram"] is True
    assert accounting_event_type(payload, normalized) == "tp2_closed"


def test_tp2_message_uses_trade_style():
    payload = {
        "event_type": "tp2_silent",
        "telegram_silent": True,
        "symbol": "DJ30",
        "side": "buy",
        "tp2": 50000,
        "profit": 92.38,
        "profit_r": 0,
        "closed_percent": 25,
        "remaining_percent": 0,
        "time": "2026-05-13T17:51:00+00:00",
    }
    normalized = normalizeNativeTradeEvent(payload)

    text = format_clean_trade_message(payload, normalized)

    assert "🎯 TP2 ВЗЯТ" in text
    assert "📊 DJ30 | BUY" in text
    assert "TP2: 50000.00" in text
    assert "Закрыто: 25% позиции" in text
    assert "💰 Зафиксировано: +92.38" in text
    assert "📊 R: 0.00R" in text
    assert "🛡 SL BE" in text
    assert "Остаток в рынке: 0%" in text
