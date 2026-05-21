import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("signal_endpoints.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["TASK_SECRET"] = "task-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.database import init_db
from server.main import app


def main():
    init_db()
    client = TestClient(app)
    payload = {
        "secret": "task-smoke-secret",
        "source": "TradingView Scanner",
        "symbol": "NAS100",
        "direction": "LONG",
        "setup": "VWAP_RECLAIM",
        "entry": 100,
        "sl": 95,
        "tp1": 108,
        "dry_run": False,
        "send": False,
    }
    blocked = client.post("/api/signals/tradingview", json={**payload, "secret": ""})
    assert blocked.status_code == 403, blocked.text
    tv = client.post("/api/signals/tradingview", json=payload)
    assert tv.status_code == 200, tv.text
    assert tv.json()["signal"]["parse_status"] == "parsed", tv.text
    manual = client.post("/api/signals/manual", json={"source_name": "TG_Channel_A", "text": "NAS100 LONG entry 100 SL 95 TP1 108", "dry_run": True, "send": False})
    assert manual.status_code == 200, manual.text
    assert manual.json()["dry_run"] is True, manual.text
    assert "raw_text" not in manual.json()["signal"], manual.text
    assert "raw_text_preview" not in manual.json()["signal"], manual.text
    scan = client.post("/api/signals/scan", json={"allow_network": False, "dry_run": True, "send": False})
    assert scan.status_code == 200, scan.text
    assert scan.json()["scanner_result"] == "unavailable", scan.text
    for path in ["/api/dashboard/signals", "/api/dashboard/signals/sources", "/api/dashboard/signals/accuracy"]:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
    print({"signal_endpoints": "ok"})


if __name__ == "__main__":
    main()
