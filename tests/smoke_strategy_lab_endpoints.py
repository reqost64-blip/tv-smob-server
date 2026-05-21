import base64
import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("strategy_lab_endpoints.sqlite3")
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


def ok_post(client, path, payload, expected=(200, 400, 403)):
    response = client.post(path, json=payload)
    assert response.status_code in expected, (path, response.status_code, response.text[:200])
    return response


def main():
    init_db()
    client = TestClient(app)
    for path in [
        "/api/health",
        "/dashboard",
        "/api/dashboard/status",
        "/api/dashboard/system",
        "/api/dashboard/trades",
        "/api/dashboard/stats",
        "/api/dashboard/account",
        "/api/dashboard/account-history",
        "/api/dashboard/bots",
        "/api/dashboard/bias",
        "/api/dashboard/storage-health",
        "/api/dashboard/strategy-lab",
        "/api/dashboard/strategy-lab/data-health",
        "/api/dashboard/strategy-lab/recommendations",
    ]:
        ok_get(client, path)
    run = ok_post(client, "/api/strategy-lab/run", {"dry_run": True, "optimize": True}, expected=(200,))
    assert run.json().get("dry_run") is True, run.text
    unsafe_run = ok_post(client, "/api/strategy-lab/run", {"dry_run": False, "optimize": True}, expected=(400,))
    assert unsafe_run.json().get("ok") is False, unsafe_run.text
    safe_bias = ok_post(client, "/api/bias/run", {"allow_network": False, "send": False}, expected=(200,))
    assert safe_bias.json().get("sent") is False, safe_bias.text
    network_bias = ok_post(client, "/api/bias/run", {"allow_network": True, "send": False}, expected=(403,))
    assert network_bias.json().get("ok") is False, network_bias.text

    secret = "do-not-leak-smoke-secret"
    ok_post(
        client,
        "/api/mt5/native-event",
        {
            "secret": secret,
            "source": "smoke",
            "bot_id": "NAS100_ORB_VWAP_RSI_OF",
            "symbol": "NAS100",
            "magic_number": 260515,
            "event_type": "heartbeat",
            "time": "2026-05-15T12:00:00+00:00",
        },
        expected=(200,),
    )
    ok_post(client, "/api/mt5/native-history", {"secret": secret, "bot_id": "NAS100_ORB_VWAP_RSI_OF", "deals": []}, expected=(200,))
    ok_post(client, "/api/mt5/native-account", {"secret": secret, "balance": 1000, "equity": 1000, "open_positions": 0}, expected=(200,))
    png_1x1 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode("ascii")
    ok_post(
        client,
        "/api/mt5/native-screenshot",
        {
            "secret": secret,
            "source": "smoke",
            "bot_id": "NAS100_ORB_VWAP_RSI_OF",
            "symbol": "NAS100",
            "event_type": "opened",
            "image_base64": png_1x1,
            "time": "2026-05-15T12:00:00+00:00",
        },
        expected=(200, 502),
    )
    print({"strategy_lab_endpoints": "ok"})


if __name__ == "__main__":
    main()
