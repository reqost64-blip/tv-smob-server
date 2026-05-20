import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("storage_health.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server import account_store as acct
from server.database import init_db
from server.main import app


def main():
    init_db()
    client = TestClient(app)
    empty = client.get("/api/dashboard/storage-health")
    assert empty.status_code == 200, empty.text
    data = empty.json()
    assert data["db_file_exists"] is True, data
    assert data["trades_total"] == 0, data
    assert "history_empty" in data["warnings"], data

    acct.save_history_deals(
        "",
        [
            {
                "deal_ticket": "storage-health-1",
                "position_id": "pos-health-1",
                "symbol": "NAS100",
                "side": "buy",
                "deal_type": "buy",
                "volume": 0.1,
                "price": 18750.0,
                "profit": 12.5,
                "commission": -1.0,
                "swap": 0,
                "deal_time": "2026-05-20 15:30:00",
                "source": "storage_health_smoke",
            }
        ],
    )
    filled = client.get("/api/dashboard/storage-health").json()
    assert filled["native_history_total"] == 1, filled
    assert filled["trades_total"] >= 1, filled
    assert filled["latest_trade_time"], filled
    print({"storage_health": "ok", "native_history_total": filled["native_history_total"]})


if __name__ == "__main__":
    main()
