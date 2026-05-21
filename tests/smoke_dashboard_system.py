import json
import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("dashboard_system.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.database import init_db
from server.main import app


def main():
    init_db()
    client = TestClient(app)
    response = client.get("/api/dashboard/system")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data
    for key in [
        "system_mode",
        "app_version",
        "dashboard_version",
        "git_commit",
        "git_branch",
        "render_service",
        "db_storage",
        "db_file",
        "uptime_seconds",
        "server_time",
    ]:
        assert key in data, key
    serialized = json.dumps(data, ensure_ascii=False).lower()
    forbidden = ["telegram_bot_token", "webhook_secret", "mt5_native_secret", "task_secret", "password", "bearer"]
    assert not any(item in serialized for item in forbidden), serialized
    assert "environment" not in data
    print({"dashboard_system": "ok", "dashboard_version": data["dashboard_version"]})


if __name__ == "__main__":
    main()
