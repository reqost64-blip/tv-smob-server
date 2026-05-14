import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def diagnostics_for(db_file_marker: str | None) -> dict:
    env = os.environ.copy()
    env["WEBHOOK_SECRET"] = "smoke-test-secret"
    env["MT5_NATIVE_SECRET"] = "do-not-leak-smoke-secret"
    env.pop("DATABASE_URL", None)
    if db_file_marker is None:
        env.pop("DB_FILE", None)
    else:
        env["DB_FILE"] = db_file_marker
    code = (
        "import json; "
        "from server import config; "
        "print(json.dumps(config.db_file_diagnostics(), sort_keys=True))"
    )
    output = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env, text=True)
    return json.loads(output)


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
    var_data_ready = Path("/var/data").exists() and os.access(Path("/var/data"), os.W_OK)
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
