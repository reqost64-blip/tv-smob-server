import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("strategy_lab_smoke.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import account_store as acct
from server.database import init_db
from server.models import NativeMT5Event
from server.strategy_test_lab import build_strategy_lab_report


def event(event_type, uid, day, minute, profit=None, **extra):
    payload = {
        "secret": "do-not-leak-smoke-secret",
        "source": "smoke",
        "bot_id": "NAS100_ORB_VWAP_RSI_OF",
        "symbol": "NAS100",
        "magic_number": 260515,
        "event_type": event_type,
        "side": "buy",
        "lot": 0.10,
        "entry": 100.0,
        "sl": 94.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "tp3": 103.0,
        "profit": profit,
        "time": f"2026-05-{day:02d}T10:{minute:02d}:00+00:00",
        "ticket": 910000 + minute,
        "trade_uid": uid,
    }
    payload.update(extra)
    return NativeMT5Event(**payload)


def save_trade(uid, day, minute, profit, tp1=True, tp2=False):
    assert acct.save_native_event(event("opened", uid, day, minute))
    if tp1:
        assert acct.save_native_event(event("tp1_be", uid, day, minute + 1, profit=10, closed_percent=60))
    if tp2:
        assert acct.save_native_event(event("tp2_silent", uid, day, minute + 2, profit=20, closed_percent=30))
    assert acct.save_native_event(event("closed_profit" if profit >= 0 else "closed_loss", uid, day, minute + 3, profit=profit))


def main():
    init_db()
    for i in range(1, 7):
        save_trade(f"smoke_win_{i}", i, 4, 10, tp1=True)
    save_trade("smoke_big_loss", 10, 4, -60, tp1=False)

    report = build_strategy_lab_report(symbol="NAS100", include_bias_filter=False)
    assert report["ok"] is True, report
    assert report["trade_count"] == 7, report["trade_count"]
    nas = report["per_symbol"]["NAS100"]
    assert nas["tp_sl_diagnostics"]["sl_tp_ratio"] == 6.0, nas["tp_sl_diagnostics"]
    assert nas["risk_damage"]["single_loss_damage_days"] == 6.0, nas["risk_damage"]
    assert nas["risk_damage"]["risk_status"] == "CRITICAL", nas["risk_damage"]
    assert report["critical_problems"], report
    print({"strategy_lab": "ok", "status": nas["risk_damage"]["risk_status"], "trades": report["trade_count"]})


if __name__ == "__main__":
    main()
