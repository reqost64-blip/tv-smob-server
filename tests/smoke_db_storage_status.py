import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def diagnostics_for(db_file_marker: str | None) -> dict:
    os.environ["WEBHOOK_SECRET"] = "smoke-test-secret"
    os.environ["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
    from server import config
    if db_file_marker is None:
        return config.db_file_diagnostics_for_path("bridge.db", configured=False, storage_source="default")
    return config.db_file_diagnostics_for_path(db_file_marker, configured=True, storage_source="DB_FILE")


def main():
    fallback = diagnostics_for(None)
    assert fallback["db_storage"] == "default", fallback
    assert fallback["db_file_configured"] is False, fallback
    assert fallback["db_persistent_expected"] is False, fallback

    tmp_status = diagnostics_for("/tmp/test_bridge.db")
    assert tmp_status["db_storage"] == "DB_FILE", tmp_status
    assert tmp_status["db_file_configured"] is True, tmp_status
    assert tmp_status["db_persistent_expected"] is False, tmp_status
    assert tmp_status["db_warning"], tmp_status

    var_status = diagnostics_for("/var/data/bridge.db")
    var_data_ready = bool(var_status["db_file_dir_exists"] and var_status["db_file_is_writable"])
    assert var_status["db_file_configured"] is True, var_status
    assert var_status["db_persistent_expected"] is var_data_ready, var_status
    if var_data_ready:
        assert var_status["db_storage"] == "render_persistent_disk", var_status
    else:
        assert var_status["db_storage"] == "DB_FILE", var_status
        assert var_status["db_warning"], var_status

    print({"db_storage_status": "ok", "var_data_ready": var_data_ready})


if __name__ == "__main__":
    main()
