import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _is_default_sqlite_path(value: str | None) -> bool:
    normalized = str(value or "").strip().replace("\\", "/")
    return normalized in {"", "bridge.db", "./bridge.db"}


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


def _resolve_db_file() -> tuple[str, str]:
    raw_db_file = os.getenv("DB_FILE")
    raw_database_url = os.getenv("DATABASE_URL")
    sqlite_url_path = _sqlite_path_from_database_url(raw_database_url)
    render_disk = Path("/var/data")

    if sqlite_url_path:
        target = Path(sqlite_url_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target), "DATABASE_URL"

    if os.getenv("RENDER") and render_disk.exists() and _is_default_sqlite_path(raw_db_file):
        target = render_disk / "bridge.db"
        _copy_seed_db_if_needed(target)
        return str(target), "render_persistent_disk"

    target = Path(raw_db_file or "bridge.db")
    if target.parent != Path("."):
        target.parent.mkdir(parents=True, exist_ok=True)
    return str(target), "DB_FILE" if raw_db_file else "default"

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
