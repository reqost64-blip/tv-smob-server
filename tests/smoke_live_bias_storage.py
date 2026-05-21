import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("live_bias_storage.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import bias_store
from server.database import init_db
from server.live_bias_engine import calculate_live_bias_report


def main():
    init_db()
    report = calculate_live_bias_report(allow_network=False)
    bias_store.save_live_bias_report(report, sent_to_telegram=False, send_reason="smoke")
    latest = bias_store.latest_live_bias()
    history = bias_store.live_bias_history(limit=20)
    assert len(latest) == len(report["symbols"]), latest
    assert len(history) >= len(report["symbols"]), history
    assert all(row["direction"] in {"LONG", "SHORT"} for row in latest), latest
    assert all("factor_scores" in row and isinstance(row["factor_scores"], dict) for row in latest), latest
    one = bias_store.latest_live_bias(symbol="NAS100")
    assert len(one) == 1 and one[0]["symbol"] == "NAS100", one
    print({"live_bias_storage": "ok", "latest": len(latest), "history": len(history)})


if __name__ == "__main__":
    main()
