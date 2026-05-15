import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("strategy_optimizer_smoke.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import account_store as acct
from server.database import init_db
from server.models import NativeMT5Event
from server.strategy_optimizer import recommendations_payload, run_strategy_lab


def event(event_type, uid, minute, profit=None, entry=100.0, sl=98.0, tp1=101.6, **extra):
    payload = {
        "secret": "do-not-leak-smoke-secret",
        "source": "smoke",
        "bot_id": "DJ30_ORB_VWAP_RSI_OF",
        "symbol": "DJ30",
        "magic_number": 260515,
        "event_type": event_type,
        "side": "buy",
        "lot": 0.10,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": 103.2,
        "tp3": 104.8,
        "profit": profit,
        "time": f"2026-05-02T11:{minute:02d}:00+00:00",
        "ticket": 920000 + minute,
        "trade_uid": uid,
    }
    payload.update(extra)
    return NativeMT5Event(**payload)


def save_trade(uid, minute, profit, tp1=True, tp2=False):
    assert acct.save_native_event(event("opened", uid, minute))
    if tp1:
        assert acct.save_native_event(event("tp1_be", uid, minute + 1, profit=16, closed_percent=60))
    if tp2:
        assert acct.save_native_event(event("tp2_silent", uid, minute + 2, profit=22, closed_percent=30))
    assert acct.save_native_event(event("closed_profit" if profit >= 0 else "closed_loss", uid, minute + 3, profit=profit))


def main():
    init_db()
    for i in range(1, 29):
        save_trade(f"smoke_opt_win_{i}", i, 24, tp1=True, tp2=i % 2 == 0)
    for i in range(1, 5):
        save_trade(f"smoke_opt_loss_{i}", 32 + i, -18, tp1=False)

    result = run_strategy_lab(symbol="DJ30", dry_run=True, optimize=True)
    assert result["ok"] is True, result
    assert result["dry_run"] is True, result
    assert result["requires_human_approval"] is True, result
    assert "DJ30" in result["recommendations"], result["recommendations"]

    recs = recommendations_payload(symbol="DJ30")
    best = recs["best_candidate_settings_per_symbol"]["DJ30"]
    assert best["settings"]["skip_bad_reward_risk"] is True, best
    assert "profit_factor" in best["metrics"], best
    print({"strategy_optimizer": "ok", "symbol": "DJ30", "score": best["score"]})


if __name__ == "__main__":
    main()
