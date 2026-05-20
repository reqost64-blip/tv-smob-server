import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("history_import.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.database import init_db
from server.main import app


SAMPLE_CSV = """Time,Position,Symbol,Type,Volume,Price,S/L,T/P,Profit,Commission,Swap,Comment,Magic,Deal,Order
2026.05.20 15:30:00,1001,NAS100,buy,0.10,18750.5,,,25.00,-1.00,0.00,recovered import,26043001,9001,8001
2026.05.20 15:45:00,1002,DJ30,sell,0.10,39010.0,,,-15.50,-1.00,0.00,recovered import,26043002,9002,8002
"""


def main():
    init_db()
    client = TestClient(app)

    dry = client.post(
        "/api/history/import?dry_run=true&source_name=smoke_csv",
        content=SAMPLE_CSV,
        headers={"content-type": "text/csv"},
    )
    assert dry.status_code == 200, dry.text
    preview = dry.json()
    assert preview["dry_run"] is True, preview
    assert preview["total_rows"] == 2, preview
    assert preview["valid_rows"] == 2, preview
    assert preview["saved_rows"] == 0, preview
    assert set(preview["symbols"]) == {"DJ30", "NAS100"}, preview
    assert client.get("/api/dashboard/storage-health").json()["native_history_total"] == 0

    blocked = client.post(
        "/api/history/import?dry_run=false",
        content=SAMPLE_CSV,
        headers={"content-type": "text/csv"},
    )
    assert blocked.status_code == 403, blocked.text

    imported = client.post(
        "/api/history/import?dry_run=false&source_name=smoke_csv",
        content=SAMPLE_CSV,
        headers={"content-type": "text/csv", "x-mt5-native-secret": "do-not-leak-smoke-secret"},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert result["dry_run"] is False, result
    assert result["saved_rows"] == 2, result

    duplicate_preview = client.post(
        "/api/history/import?dry_run=true&source_name=smoke_csv",
        content=SAMPLE_CSV,
        headers={"content-type": "text/csv"},
    ).json()
    assert duplicate_preview["duplicates_estimate"] == 2, duplicate_preview
    assert duplicate_preview["importable_rows"] == 0, duplicate_preview

    health = client.get("/api/dashboard/storage-health").json()
    assert health["native_history_total"] == 2, health
    assert health["trades_total"] >= 2, health

    lab = client.get("/api/dashboard/strategy-lab").json()
    assert lab["trade_count"] == 2, lab
    data_health = client.get("/api/dashboard/strategy-lab/data-health").json()
    assert data_health["trade_count"] == 2, data_health
    print({"history_import": "ok", "imported": result["saved_rows"], "trade_count": lab["trade_count"]})


if __name__ == "__main__":
    main()
