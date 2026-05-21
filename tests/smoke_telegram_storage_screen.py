import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_storage_screen():
    text, markup = bot.render_menu_callback("refresh_storage", "smoke")
    assert "keyboard" not in (markup or {})
    assert "СОСТОЯНИЕ БАЗЫ" in text
    assert "Путь:" in text
    assert "/var/data/bridge.db" in text or "—" in text


if __name__ == "__main__":
    test_storage_screen()
    print("ok")
