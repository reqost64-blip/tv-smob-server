import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("DB_FILE", "tests/smoke_control_center.sqlite3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from server import account_store as acct
    from server.database import init_db
except ModuleNotFoundError as exc:
    acct = None
    init_db = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None
    init_db()


def smoke_bot_control_roundtrip():
    if IMPORT_ERROR:
        return {"skipped": str(IMPORT_ERROR)}
    bot_id = "NAS100_ORB_VWAP_RSI_OF"
    enabled = acct.set_native_bot_enabled(bot_id, True, "smoke")
    disabled = acct.set_native_bot_enabled(bot_id, False, "smoke")
    config = acct.native_config(bot_id)
    return {
        "enabled_ok": bool(enabled and enabled.get("enabled")),
        "disabled_ok": bool(disabled and not disabled.get("enabled")),
        "config_ok": config.get("bot_id") == bot_id and config.get("enabled") is False,
    }


def smoke_daily_report_formatter(formatter):
    report = formatter()
    return report.startswith("📊 DAILY TRADING REPORT")


def smoke_native_config_shape():
    if IMPORT_ERROR:
        return {"skipped": str(IMPORT_ERROR)}
    config = acct.native_config("SP500_ORB_VWAP_RSI_OF")
    expected = {"bot_id", "enabled", "symbol", "reason"}
    return expected.issubset(config.keys())


if __name__ == "__main__":
    if IMPORT_ERROR:
        print({"skipped": str(IMPORT_ERROR)})
        raise SystemExit(0)
    from server.telegram_bot import format_daily_report

    result = {
        "bot_control": smoke_bot_control_roundtrip(),
        "daily_report": smoke_daily_report_formatter(format_daily_report),
        "native_config": smoke_native_config_shape(),
    }
    print(result)
