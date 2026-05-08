import os
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
MT5_NATIVE_SECRET: str = os.getenv("MT5_NATIVE_SECRET") or WEBHOOK_SECRET
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./bridge.db")
DB_FILE: str = os.getenv("DB_FILE", "bridge.db")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
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
