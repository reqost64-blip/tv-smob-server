import os
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./bridge.db")
DB_FILE: str = os.getenv("DB_FILE", "bridge.db")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
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

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set in environment")
