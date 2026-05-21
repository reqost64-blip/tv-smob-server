import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).with_name("live_bias_engine.sqlite3")
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
os.environ["DB_FILE"] = str(DB_PATH)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.database import init_db
from server.live_bias_engine import BIAS_SYMBOLS, calculate_live_bias_report, format_live_bias_telegram_message


def main():
    init_db()
    report = calculate_live_bias_report(allow_network=False)
    assert report["version"] == "live-bias-v2", report
    assert len(report["symbols"]) == len(BIAS_SYMBOLS), report
    for row in report["symbols"]:
        assert row["direction"] in {"LONG", "SHORT"}, row
        assert row["bias"] in {"LONG", "SHORT"}, row
        assert row["direction"] != "CONSOLIDATION", row
        assert 51 <= int(row["confidence"]) <= 85, row
        assert row["strength"] in {"WEAK", "MEDIUM", "STRONG", "VERY STRONG"}, row
        assert row["risk"] in {"LOW", "MEDIUM", "HIGH"}, row
        assert isinstance(row["factor_scores"], dict), row
        assert isinstance(row["source_availability"], dict), row
        assert isinstance(row["reasons"], list), row
    text = format_live_bias_telegram_message(report)
    assert "LIVE MARKET BIAS" in text, text
    assert "CONSOLIDATION" not in text, text
    assert "NAS100" in text and "BTCUSD" in text, text
    print({"live_bias_engine": "ok", "symbols": len(report["symbols"])})


if __name__ == "__main__":
    main()
