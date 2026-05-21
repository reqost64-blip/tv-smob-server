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
        ["📊 Счёт и позиции"],
        ["📈 Байес", "⚡ Сигналы"],
        ["🧾 Сделки", "📉 Аналитика"],
        ["🌐 Открыть SMOB"],
    ]
    labels = {item for row in rows(telegram_bot.dashboard_keyboard()) for item in row}
    for old in ("🌍 Рынок", "Действия", "🏆 Рекорды", "🤖 Боты", "🎛 Пульт", "📊 Статистика", "🛡 Риск", "🧠 Sources", "🌐 Сайт"):
        assert old not in labels
    assert telegram_bot.normalize_dashboard_button("⚡ Сигналы") == "/signals"
    assert "СИГНАЛЫ" in telegram_bot.handle_command("/signals")
    assert "АНАЛИТИКА" in telegram_bot.handle_command("/stats")
    assert "ИСТОЧНИКИ СИГНАЛОВ" in telegram_bot.handle_command("/sources")
    text, keyboard = telegram_bot.render_command_center()
    assert "ПАНЕЛЬ SMOB" in text
    assert keyboard["inline_keyboard"][0][0]["text"] == "📊 Счёт и позиции"
    assert keyboard["inline_keyboard"][3][0]["url"] == telegram_bot.DASHBOARD_URL
    print({"telegram_signal_menu": "ok"})


if __name__ == "__main__":
    main()
