import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("persistent_history.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import account_store as acct
from server.database import init_db
from server.models import NativeMT5Event
from server.native_trade_notifications import format_clean_trade_message, normalizeNativeTradeEvent


def event(event_type, trade_uid, minute, **extra):
    payload = {
        "secret": "do-not-leak-smoke-secret",
        "source": "smoke",
        "bot_id": "DJ30_ORB_VWAP_RSI_OF",
        "symbol": "DJ30",
        "magic_number": 260514,
        "event_type": event_type,
        "side": "buy",
        "lot": 0.10,
        "entry": 49900,
        "sl": 49800,
        "tp1": 49950,
        "tp2": 50000,
        "time": f"2026-05-13T18:{minute:02d}:00+00:00",
        "ticket": 880000 + minute,
        "trade_uid": trade_uid,
    }
    payload.update(extra)
    return NativeMT5Event(**payload)


def save_full_trade(trade_uid, base_minute, profit):
    assert acct.save_native_event(event("opened", trade_uid, base_minute))
    assert acct.save_native_event(event("tp1_be", trade_uid, base_minute + 1, profit=30, closed_percent=75))
    assert acct.save_native_event(
        event(
            "tp2_silent",
            trade_uid,
            base_minute + 2,
            profit=profit - 30,
            closed_percent=25,
            telegram_silent=True,
        )
    )
    assert acct.save_native_event(event("closed_profit", trade_uid, base_minute + 3, profit=profit))


def assert_trade_state(expected_count):
    trades = acct.get_trades_filtered(source="bot", period="all", asset="ALL", limit=100, offset=0)
    assert len(trades) == expected_count, trades
    stats = acct.get_stats_filtered(source="bot", period="all", asset="ALL")
    assert stats["total_trades"] == expected_count, stats
    latest = trades[0]
    assert latest["tp1_hit"] is True
    assert latest["tp2_hit"] is True
    assert latest["status"] in {"win", "closed"}
    return trades, stats


def main():
    init_db()
    save_full_trade("smoke_persist_old", 10, 100)
    old_trades, _ = assert_trade_state(1)

    # Simulate a backend/store restart: connections are closed and init_db runs
    # again against the same configured DB file. No in-memory trade list is used.
    init_db()
    after_restart, _ = assert_trade_state(1)
    assert old_trades[0]["bot_id"] == after_restart[0]["bot_id"]

    save_full_trade("smoke_persist_new", 20, 120)
    trades, stats = assert_trade_state(2)
    assert {row["bot_id"] for row in trades} == {"DJ30_ORB_VWAP_RSI_OF"}
    assert stats["tp1_hit_rate"] == 100.0
    assert stats["tp2_hit_rate"] == 100.0

    normalized = normalizeNativeTradeEvent(
        {
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
    )
    assert normalized["shouldNotifyTelegram"] is True
    assert "🎯 TP2 ВЗЯТ" in format_clean_trade_message(normalized["rawPayload"], normalized)
    print({"persistent_history": "ok", "trades": len(trades), "db": str(DB_PATH)})


if __name__ == "__main__":
    main()
