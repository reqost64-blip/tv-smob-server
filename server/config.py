import os
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PERSISTENT_DB_PREFIX = "/var/data/"


def _is_default_sqlite_path(value: str | None) -> bool:
    normalized = str(value or "").strip().replace("\\", "/")
    return normalized in {"", "bridge.db", "./bridge.db"}


def _is_render_persistent_path(value: str | None) -> bool:
    normalized = str(value or "").strip().replace("\\", "/")
    return normalized == "/var/data/bridge.db" or normalized.startswith(PERSISTENT_DB_PREFIX)


def _sqlite_path_from_database_url(value: str | None) -> str | None:
    url = str(value or "").strip()
    if not url.startswith("sqlite:///"):
        return None
    path = url.removeprefix("sqlite:///")
    if path.startswith("./"):
        return path
    return path or None


def _copy_seed_db_if_needed(target: Path) -> None:
    source = Path("bridge.db")
    try:
        if target.exists() or not source.exists() or source.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError:
        return


def _ensure_parent_dir(target: Path) -> None:
    if target.parent == Path(".") or _is_render_persistent_path(str(target)):
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return


def _path_writable(path: Path) -> bool:
    directory = path if path.is_dir() else path.parent
    if not directory.exists() or not directory.is_dir():
        return False
    if os.name == "nt" and str(path).replace("\\", "/").startswith("/"):
        return os.access(directory, os.W_OK)
    try:
        with tempfile.NamedTemporaryFile(prefix=".db_write_test_", dir=directory, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
            handle.seek(0)
            return handle.read() == b"ok"
    except OSError:
        return False


def _persistent_db_ready(path: Path) -> bool:
    return _is_render_persistent_path(str(path)) and path.parent.exists() and _path_writable(path)


def _resolve_db_file() -> tuple[str, str]:
    raw_db_file = os.getenv("DB_FILE")
    raw_database_url = os.getenv("DATABASE_URL")
    sqlite_url_path = _sqlite_path_from_database_url(raw_database_url)

    if sqlite_url_path:
        target = Path(sqlite_url_path)
        _ensure_parent_dir(target)
        return str(target), "DATABASE_URL"

    target = Path(raw_db_file or "bridge.db")
    _ensure_parent_dir(target)
    if raw_db_file and _persistent_db_ready(target):
        _copy_seed_db_if_needed(target)
        return str(target), "render_persistent_disk"
    return str(target), "DB_FILE" if raw_db_file else "default"


def db_file_diagnostics() -> dict:
    return db_file_diagnostics_for_path(DB_FILE, configured=bool(os.getenv("DB_FILE")), storage_source=DB_STORAGE_SOURCE)


def db_file_diagnostics_for_path(db_file: str, configured: bool, storage_source: str = "DB_FILE") -> dict:
    path = Path(db_file)
    dir_path = path.parent if path.parent != Path("") else Path(".")
    persistent_path = _is_render_persistent_path(str(path))
    dir_exists = dir_path.exists() and dir_path.is_dir()
    file_exists = path.exists()
    writable = _path_writable(path)
    size = None
    try:
        size = path.stat().st_size if file_exists else 0
    except OSError:
        size = None

    warning = None
    if configured and not persistent_path:
        warning = "DB_FILE is not under /var/data; Render trade history may be ephemeral."
    elif not configured and not persistent_path:
        warning = "DB_FILE is not set; using fallback SQLite path that may be ephemeral on Render."
    elif persistent_path and not dir_exists:
        warning = "/var/data directory does not exist; attach a Render persistent disk."
    elif persistent_path and not writable:
        warning = "/var/data is not writable; persistent SQLite is not ready."

    persistent_ready = configured and persistent_path and dir_exists and writable
    return {
        "db_storage": "render_persistent_disk" if persistent_ready else storage_source,
        "db_file_configured": configured,
        "db_file_path": str(path),
        "db_file_exists": file_exists,
        "db_file_dir_exists": dir_exists,
        "db_file_is_writable": writable,
        "db_file_size": size,
        "db_persistent_expected": persistent_ready,
        "db_warning": warning,
    }

WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
MT5_NATIVE_SECRET: str = os.getenv("MT5_NATIVE_SECRET") or WEBHOOK_SECRET
TASK_SECRET: str = os.getenv("TASK_SECRET", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./bridge.db")
DB_FILE, DB_STORAGE_SOURCE = _resolve_db_file()
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_TRADE_CLEAN_MODE: bool = os.getenv("TELEGRAM_TRADE_CLEAN_MODE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TELEGRAM_DEBUG_TRADE_EVENTS: bool = os.getenv("TELEGRAM_DEBUG_TRADE_EVENTS", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
OPENAI_TIMEOUT_SECONDS: int = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
ENABLE_AI_WEB_SEARCH: bool = os.getenv("ENABLE_AI_WEB_SEARCH", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYSTEM_MODE: str = os.getenv("SYSTEM_MODE", "NATIVE_MT5_ONLY").strip().upper()
SUPPORTED_SYSTEM_MODES: set[str] = {"TRADINGVIEW_BRIDGE", "NATIVE_MT5_ONLY"}

if SYSTEM_MODE not in SUPPORTED_SYSTEM_MODES:
    raise RuntimeError(
        f"Unsupported SYSTEM_MODE '{SYSTEM_MODE}'. "
        f"Supported modes: {', '.join(sorted(SUPPORTED_SYSTEM_MODES))}"
    )

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set in environment")

if not MT5_NATIVE_SECRET:
    raise RuntimeError("MT5_NATIVE_SECRET or WEBHOOK_SECRET is not set in environment")


def is_native_mt5_only() -> bool:
    return SYSTEM_MODE == "NATIVE_MT5_ONLY"
