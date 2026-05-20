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


BASE = {
    "bot_id": "DJ30_ORB_VWAP_RSI_OF",
    "symbol": "DJ30",
    "side": "buy",
    "lot": 1.0,
    "entry": 49620.40,
    "sl": 49510.00,
    "tp1": 49696.55,
    "tp2": 49912.30,
    "tp3": 50120.00,
    "time": "2026-05-13T17:42:00+00:00",
}


def render(payload):
    normalized = normalizeNativeTradeEvent(payload)
    return normalized, format_clean_trade_message(payload, normalized)


def test_open_notification_render():
    normalized, text = render({**BASE, "event_type": "opened", "risk_r": 1.0})
    assert normalized["telegramTemplate"] == "opened"
    assert "✅ СДЕЛКА ОТКРЫТА" in text
    assert "DJ30 | BUY" in text
    assert "Вход: 49620.40" in text
    assert "SL: 49510.00" in text
    assert "TP1 ⬜ 49696.55" in text
    assert "TP2 ⬜ 49912.30" in text
    assert "TP3 ⬜ 50120.00" in text
    assert "Риск: 1.00R" in text
    assert "Объём: 1.00 lot" in text
    assert "🕒 19:42 13.05.2026" in text


def test_tp1_notification_render():
    _, text = render(
        {
            **BASE,
            "event_type": "tp1_be",
            "profit": 92.38,
            "profit_r": 0.84,
            "closed_percent": 75,
            "remaining_percent": 25,
            "time": "2026-05-13T17:51:00+00:00",
        }
    )
    assert "🎯 TP1 ВЗЯТ ✅" in text
    assert "TP1: 49696.55" in text
    assert "Закрыто: 75%" in text
    assert "Прибыль: +€92.38" in text
    assert "R: +0.84R" in text
    assert "TP1 ✅" in text
    assert "TP2 ⬜" in text
    assert "TP3 ⬜" in text
    assert "SL → BE" in text
    assert "Остаток: 25%" in text


def test_tp2_notification_render():
    payload = {
        **BASE,
        "event_type": "tp2_silent",
        "telegram_silent": True,
        "tp1_done": True,
        "profit": 48.60,
        "accumulated_profit": 140.98,
        "closed_percent": 20,
        "remaining_percent": 5,
        "time": "2026-05-13T17:58:00+00:00",
    }
    normalized, text = render(payload)

    assert normalized["normalizedType"] == "trade_tp2"
    assert normalized["telegramTemplate"] == "tp2"
    assert normalized["shouldNotifyTelegram"] is True
    assert accounting_event_type(payload, normalized) == "tp2_closed"
    assert "🎯 TP2 ВЗЯТ ✅" in text
    assert "TP2: 49912.30" in text
    assert "Прибыль: +€48.60" in text
    assert "Всего: +€140.98" in text
    assert "TP1 ✅" in text
    assert "TP2 ✅" in text
    assert "TP3 ⬜" in text


def test_full_close_notification_render():
    normalized, text = render(
        {
            **BASE,
            "event_type": "tp3_closed",
            "tp1_done": True,
            "tp2_done": True,
            "profit": 156.40,
            "profit_r": 1.42,
            "duration_minutes": 24,
            "time": "2026-05-13T18:06:00+00:00",
        }
    )
    assert normalized["normalizedType"] == "trade_tp3"
    assert normalized["telegramTemplate"] == "closed"
    assert normalized["shouldNotifyTelegram"] is True
    assert "✅ СДЕЛКА ЗАКРЫТА" in text
    assert "Итог: +€156.40" in text
    assert "R: +1.42R" in text
    assert "TP1 ✅" in text
    assert "TP2 ✅" in text
    assert "TP3 ✅" in text
    assert "Время в сделке: 24м" in text


def test_sl_notification_render():
    _, text = render(
        {
            **BASE,
            "symbol": "NAS100",
            "side": "sell",
            "event_type": "closed_loss",
            "profit": -126.50,
            "profit_r": -1.0,
            "time": "2026-05-13T18:14:00+00:00",
        }
    )
    assert "🛑 STOP LOSS" in text
    assert "NAS100 | SELL" in text
    assert "Убыток: -€126.50" in text
    assert "R: -1.00R" in text
    assert "TP1 ⬜" in text
    assert "TP2 ⬜" in text
    assert "TP3 ⬜" in text


def test_be_notification_render():
    normalized, text = render(
        {
            **BASE,
            "event_type": "be_moved",
            "tp1_done": True,
            "new_sl": 49620.40,
            "closed_percent": 75,
            "time": "2026-05-13T17:52:00+00:00",
        }
    )
    assert normalized["telegramTemplate"] == "be"
    assert normalized["shouldNotifyTelegram"] is True
    assert accounting_event_type(BASE | {"event_type": "be_moved"}, normalized) == "be_moved"
    assert "🛡 SL В БУ" in text
    assert "SL перенесён в безубыток" in text
    assert "Новый SL: 49620.40" in text
    assert "TP1 ✅" in text
    assert "TP2 ⬜" in text
    assert "TP3 ⬜" in text
    assert "Остаток: 25%" in text


def test_missing_tp2_tp3_are_hidden():
    _, text = render(
        {
            "event_type": "opened",
            "symbol": "DJ30",
            "side": "buy",
            "entry": 49620.40,
            "sl": 49510.00,
            "tp1": 49696.55,
            "tp2_enabled": False,
            "time": "2026-05-13T17:42:00+00:00",
        }
    )
    assert "TP1 ⬜ 49696.55" in text
    assert "TP2" not in text
    assert "TP3" not in text


def test_missing_fields_render_dash():
    _, text = render({"event_type": "opened", "symbol": "DJ30", "side": "buy", "tp1": 49696.55})
    assert "Вход: —" in text
    assert "SL: —" in text
    assert "Риск: —" in text
    assert "Объём: —" in text


def test_tp2_event_types_still_recognized():
    for event_type in ("tp2_hit", "tp2_taken", "tp2_closed", "tp2_silent", "partial_close_tp2"):
        payload = {**BASE, "event_type": event_type, "telegram_silent": True}
        normalized = normalizeNativeTradeEvent(payload)
        assert normalized["normalizedType"] == "trade_tp2"
        assert normalized["telegramTemplate"] == "tp2"
        assert normalized["shouldNotifyTelegram"] is True
        assert accounting_event_type(payload, normalized) == "tp2_closed"


def main():
    test_open_notification_render()
    test_tp1_notification_render()
    test_tp2_notification_render()
    test_full_close_notification_render()
    test_sl_notification_render()
    test_be_notification_render()
    test_missing_tp2_tp3_are_hidden()
    test_missing_fields_render_dash()
    test_tp2_event_types_still_recognized()
    print({"native_trade_notifications": "ok"})


if __name__ == "__main__":
    main()
