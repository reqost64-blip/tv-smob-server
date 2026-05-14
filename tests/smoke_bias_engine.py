import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("bias_smoke.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.bias_engine import BIAS_SYMBOLS, calculate_bias_report, format_bias_telegram_message
from server.bias_store import latest_bias_report, save_bias_report
from server.database import init_db


def main():
    init_db()
    report = calculate_bias_report(allow_network=False)
    assert len(report["symbols"]) == len(BIAS_SYMBOLS), report
    assert report["macro_risk"] in {"LOW", "MEDIUM", "HIGH"}, report
    assert all(row["bias"] in {"LONG", "SHORT", "CONSOLIDATION"} for row in report["symbols"])
    text = format_bias_telegram_message(report)
    assert "📊 NY PRE-MARKET BIAS" in text
    assert "NAS100:" in text
    saved = save_bias_report(report)
    loaded = latest_bias_report()
    assert loaded and loaded["id"] == saved["id"]
    assert len(loaded["symbols"]) == len(BIAS_SYMBOLS)
    print({"bias_engine": "ok", "symbols": len(loaded["symbols"])})


if __name__ == "__main__":
    main()
