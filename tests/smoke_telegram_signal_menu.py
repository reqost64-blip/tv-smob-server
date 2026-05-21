import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("telegram_signal_menu.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot
from server.database import init_db


def rows(markup):
    return [[button.get("text") if isinstance(button, dict) else button for button in row] for row in markup.get("keyboard", [])]


def main():
    init_db()
    assert rows(telegram_bot.dashboard_keyboard()) == [
        ["🎛 Пульт", "📈 Bias"],
        ["⚡ Сигналы", "🧾 Сделки"],
        ["📊 Статистика", "🛡 Риск"],
        ["🧠 Sources", "🌐 Сайт"],
    ]
    labels = {item for row in rows(telegram_bot.dashboard_keyboard()) for item in row}
    for old in ("🌍 Рынок", "Действия", "🏆 Рекорды", "🤖 Боты"):
        assert old not in labels
    assert telegram_bot.normalize_dashboard_button("⚡ Сигналы") == "/signals"
    assert "SIGNAL BOARD" in telegram_bot.handle_command("/signals")
    assert "SYSTEM STATISTICS" in telegram_bot.handle_command("/stats")
    assert "SIGNAL SOURCES" in telegram_bot.handle_command("/sources")
    print({"telegram_signal_menu": "ok"})


if __name__ == "__main__":
    main()
