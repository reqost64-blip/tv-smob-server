import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("dashboard_routes.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.database import init_db
from server.main import app


def ok_get(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:200])
    return response


def main():
    init_db()
    client = TestClient(app)
    pages = [
        "/dashboard",
        "/dashboard/portfolio",
        "/dashboard/positions",
        "/dashboard/trades",
        "/dashboard/history",
        "/dashboard/analytics",
        "/dashboard/risk",
        "/dashboard/bias",
        "/dashboard/lab",
        "/dashboard/storage",
        "/dashboard/settings",
    ]
    for page in pages:
        response = ok_get(client, page)
        assert "MT5 Command" in response.text, page
    for endpoint in [
        "/api/health",
        "/api/dashboard/status",
        "/api/dashboard/account",
        "/api/dashboard/account-history",
        "/api/dashboard/trades",
        "/api/dashboard/stats",
        "/api/dashboard/bots",
        "/api/dashboard/bias",
        "/api/dashboard/bias/live",
        "/api/dashboard/bias/live/history",
        "/api/dashboard/bias/live/accuracy",
        "/api/dashboard/bias/live/calibration",
        "/api/dashboard/strategy-lab",
        "/api/dashboard/strategy-lab/recommendations",
        "/api/dashboard/strategy-lab/data-health",
        "/api/dashboard/storage-health",
        "/api/dashboard/system",
    ]:
        ok_get(client, endpoint)
    print({"dashboard_routes": "ok", "pages": len(pages)})


if __name__ == "__main__":
    main()
