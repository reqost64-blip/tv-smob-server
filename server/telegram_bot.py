import json
import urllib.parse
import urllib.request
from typing import Optional

from . import config
from . import queue as q


NOTIFY_EXECUTION_STATUSES = {
    "open_failed",
    "opened",
    "tp1_closed",
    "tp2_closed",
    "tp3_closed",
    "be_moved",
    "position_closed",
}


def send_telegram_message(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
        }
    ).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            return True
    except Exception:
        return False


def notify_event(event_type: str, signal_id: Optional[str] = None, details: Optional[str] = None) -> None:
    title = event_type.replace("_", " ")
    parts = [f"TV-MT5: {title}"]
    if signal_id:
        parts.append(f"signal_id: {signal_id}")
    if details:
        parts.append(details)
    q.record_event(event_type, signal_id, {"details": details})
    send_telegram_message("\n".join(parts))


def format_execution_report(report: Optional[dict]) -> str:
    if not report:
        return "No execution reports yet."

    lines = [
        "Last execution report",
        f"signal_id: {report.get('signal_id')}",
        f"status: {report.get('status')}",
    ]
    if report.get("ticket") is not None:
        lines.append(f"ticket: {report.get('ticket')}")
    if report.get("executed_price") is not None:
        lines.append(f"price: {report.get('executed_price')}")
    if report.get("executed_at"):
        lines.append(f"executed_at: {report.get('executed_at')}")
    if report.get("message"):
        lines.append(f"message: {report.get('message')}")
    return "\n".join(lines)


def handle_command(text: str) -> str:
    command = text.strip().split()[0].lower() if text.strip() else ""

    if command == "/status":
        counts = q.command_counts()
        last_report = q.last_execution_report()
        return "\n".join(
            [
                "Server status",
                "server: running",
                f"trading: {'enabled' if config.TRADING_ENABLED else 'disabled'}",
                f"queued commands: {counts.get('queued', 0)}",
                f"sent commands: {counts.get('sent', 0)}",
                f"acknowledged commands: {counts.get('acknowledged', 0)}",
                f"last signal id: {q.last_signal_id() or 'none'}",
                "last execution report:",
                format_execution_report(last_report),
            ]
        )

    if command == "/last_trade":
        return format_execution_report(q.last_execution_report())

    if command == "/today":
        summary = q.today_summary()
        estimated_pnl = summary["estimated_pnl"]
        return "\n".join(
            [
                "Today",
                f"signals: {summary['signals']}",
                f"opened: {summary['opened']}",
                f"rejected: {summary['rejected']}",
                f"estimated PnL: {estimated_pnl if estimated_pnl is not None else 'n/a'}",
            ]
        )

    if command == "/help":
        return "\n".join(
            [
                "Commands",
                "/status - server and queue status",
                "/last_trade - latest execution report",
                "/today - today's signal summary",
                "/help - command list",
            ]
        )

    return "Unknown command. Use /help."


def parse_telegram_update(update: dict) -> tuple[Optional[str], Optional[str]]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return (str(chat_id) if chat_id is not None else None, text)
