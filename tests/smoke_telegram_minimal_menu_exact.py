import os
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import telegram_bot as bot


EXPECTED_ROWS = [
    ["📊 Статус", "🧾 Сделки"],
    ["📈 Bias", "🌐 Сайт"],
]
OLD_BUTTONS = {"🎛 Пульт", "⚡ Сигналы", "📊 Статистика", "🛡 Риск", "🧠 Sources", "🌍 Рынок", "Действия", "🏆 Рекорды", "🤖 Боты"}


def keyboard_rows(markup):
    rows = []
    for row in markup.get("keyboard", []):
        rows.append([button.get("text") if isinstance(button, dict) else str(button) for button in row])
    return rows


def inline_labels(markup):
    return [button.get("text") for row in markup.get("inline_keyboard", []) for button in row]


def test_reply_keyboard_exact():
    keyboard = bot.dashboard_keyboard()
    assert keyboard_rows(keyboard) == EXPECTED_ROWS
    assert not (OLD_BUTTONS & set(sum(keyboard_rows(keyboard), [])))


def test_status_inline_exact():
    _, markup = bot.render_menu_callback("refresh_center", "smoke")
    assert "keyboard" not in markup
    assert inline_labels(markup) == ["🔄 Обновить", "🧾 Сделки", "📈 Bias", "🌐 Dashboard"]


def test_only_allowed_commands_route_to_screens():
    assert bot._command_to_callback("/status") == "refresh_center"
    assert bot._command_to_callback("/trades") == "refresh_trades"
    assert bot._command_to_callback("/bias") == "refresh_bias"
    for command in ("/signals", "/stats", "/risk", "/sources", "/storage", "/lab", "/live_bias"):
        assert bot._command_to_callback(command) is None
        assert "отключён" in bot.handle_command(command)


if __name__ == "__main__":
    test_reply_keyboard_exact()
    test_status_inline_exact()
    test_only_allowed_commands_route_to_screens()
    print("ok")
