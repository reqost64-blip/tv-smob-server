import os
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./bridge.db")
DB_FILE: str = os.getenv("DB_FILE", "bridge.db")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set in environment")
