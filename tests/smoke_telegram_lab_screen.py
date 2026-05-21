import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


def test_lab_screen():
    text, markup = bot.render_menu_callback("refresh_lab", "smoke")
    assert "keyboard" not in (markup or {})
    assert "ЛАБОРАТОРИЯ СТРАТЕГИИ" in text
    assert "Сделок:" in text
    assert "Риск:" in text


if __name__ == "__main__":
    test_lab_screen()
    print("ok")
