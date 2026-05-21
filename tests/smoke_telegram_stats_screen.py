import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("telegram_stats_screen.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.telegram_bot import handle_command, render_system_statistics_screen
from server.database import init_db


def main():
    init_db()
    text, keyboard = render_system_statistics_screen()
    assert "АНАЛИТИКА" in text, text
    assert "Сигналы:" in text and "Байес:" in text and "Источники:" in text, text
    assert "АНАЛИТИКА" in handle_command("/stats")
    assert keyboard.get("inline_keyboard"), keyboard
    print({"telegram_stats_screen": "ok"})


if __name__ == "__main__":
    main()
