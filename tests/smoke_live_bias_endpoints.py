import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("live_bias_endpoints.sqlite3")
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
    run = client.post("/api/bias/live/run", json={"allow_network": False, "send": False})
    assert run.status_code == 200, run.text
    data = run.json()
    assert data["sent"] is False, data
    assert data["report"]["directions_long_short_only"] is True, data
    assert len(data["report"]["symbols"]) >= 6, data
    assert all(row["direction"] in {"LONG", "SHORT"} for row in data["report"]["symbols"]), data

    live = client.get("/api/dashboard/bias/live")
    assert live.status_code == 200, live.text
    live_data = live.json()
    assert live_data["bias"]["directions_long_short_only"] is True, live_data
    assert len(live_data["bias"]["symbols"]) >= 6, live_data

    history = client.get("/api/dashboard/bias/live/history?limit=10")
    assert history.status_code == 200, history.text
    assert history.json()["count"] > 0, history.text
    accuracy = client.get("/api/dashboard/bias/live/accuracy?limit=50")
    assert accuracy.status_code == 200, accuracy.text
    assert "overall" in accuracy.json(), accuracy.text
    calibration = client.get("/api/dashboard/bias/live/calibration?limit=50")
    assert calibration.status_code == 200, calibration.text
    assert calibration.json()["requires_human_approval"] is True, calibration.text

    blocked_send = client.post("/api/bias/live/run", json={"allow_network": False, "send": True})
    assert blocked_send.status_code == 403, blocked_send.text
    safe_network = client.post("/api/bias/live/run", json={"allow_network": True, "send": False, "symbol": "NAS100"})
    assert safe_network.status_code == 200, safe_network.text
    print({"live_bias_endpoints": "ok"})


if __name__ == "__main__":
    main()
