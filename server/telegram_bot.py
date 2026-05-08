import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import account_store as acct
from . import config
from . import queue as q
from .ai_command_parser import SYMBOLS, parse_natural_language_command
from .ai_web_research import (
    answer_with_web_search,
    get_asset_impact_summary,
    get_economic_calendar_today,
    get_market_news_today,
    get_market_today_summary,
)
from .models import NativeMT5Event, WebhookPayload
from .settings_store import (
    approve_pending_approval,
    create_pending_approval,
    get_setting,
    list_pending_approvals,
    reject_pending_approval,
)


BERLIN_TZ = ZoneInfo("Europe/Berlin")
DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
THIN_DIVIDER = "────────────────────"
DEFAULT_ASSETS_LINE = "XAUUSD · NAS100 · DJ30 · US500 · BTCUSD · GER40FT"

ALLOWED_TRADE_STATUSES = {
    "opened",
    "dry_run_open",
    "tp1_closed",
    "tp2_closed",
    "tp3_closed",
    "be_moved",
    "position_closed",
    "closed_by_signal",
    "dry_run_close",
    "open_failed",
    "close_failed",
    "rejected",
    "close_rejected",
}
NOTIFY_EXECUTION_STATUSES = ALLOWED_TRADE_STATUSES
NATIVE_MT5_TELEGRAM_EVENTS = {
    "opened",
    "tp1_closed",
    "tp2_closed",
    "tp3_closed",
    "be_moved",
    "position_closed",
    "closed_by_signal",
    "open_failed",
    "close_failed",
    "error",
}
NATIVE_NO_DATA_MESSAGE = "Данных от native MT5 bot пока нет."

OPEN_EXECUTION_STATUSES = {"opened", "dry_run_open"}
TP_EXECUTION_STATUSES = {"tp1_closed", "tp2_closed", "tp3_closed"}
CLOSE_EXECUTION_STATUSES = {"position_closed", "closed_by_signal", "dry_run_close"}
ERROR_EXECUTION_STATUSES = {"open_failed", "close_failed", "rejected", "close_rejected"}

KNOWN_SETTING_KEYS = {
    "trading_enabled",
    "dry_run",
    "use_server_lot",
    "global_lot_multiplier",
    "max_lot",
    "max_daily_loss",
    "max_trades_per_day",
    "allowed_symbols",
    "symbol_lot_multiplier_XAUUSD",
    "symbol_lot_multiplier_NAS100",
    "symbol_lot_multiplier_DJ30",
    "symbol_lot_multiplier_US500",
    "symbol_lot_multiplier_BTCUSD",
    "symbol_paused_until_XAUUSD",
    "symbol_paused_until_NAS100",
    "symbol_paused_until_DJ30",
    "symbol_paused_until_US500",
    "symbol_paused_until_BTCUSD",
}


def allow_real_trading():
    return os.getenv("ALLOW_REAL_TRADING", "false").lower() == "true"


def send_telegram_message(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "reply_markup": json.dumps(dashboard_keyboard(), ensure_ascii=False),
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            return True
    except Exception:
        return False


def send_telegram_photo(photo_path, caption: str = "") -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_CHAT_ID:
        return False
    boundary = "----tvsmob" + os.urandom(12).hex()
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto"
    fields = {
        "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
        "caption": str(caption or "")[:1024],
        "reply_markup": json.dumps(dashboard_keyboard(), ensure_ascii=False),
    }
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    filename = os.path.basename(str(photo_path)) or "screenshot.png"
    try:
        with open(photo_path, "rb") as photo_file:
            photo_bytes = photo_file.read()
    except OSError:
        return False
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            'Content-Disposition: form-data; name="photo"; '
            f'filename="{filename}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(photo_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    try:
        request = urllib.request.Request(
            url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=10):
            return True
    except Exception:
        return False


def should_notify_execution(status: str) -> bool:
    return str(status or "").strip().lower() in ALLOWED_TRADE_STATUSES


def notify_event(event_type: str, signal_id: Optional[str] = None, details: Optional[str] = None) -> None:
    q.record_event(event_type, signal_id, {"details": details})


def notify_close_signal(payload: WebhookPayload) -> None:
    symbol = payload.mt5_symbol or payload.symbol
    q.record_event(
        "close_signal_received",
        payload.signal_id,
        {
            "symbol": symbol,
            "side": payload.side,
            "reason": payload.reason,
            "parent_signal_id": payload.parent_signal_id,
        },
    )


def notify_execution(status: str, report) -> None:
    status = str(status or "").strip().lower()
    payload = q.get_command_payload(report.signal_id)
    q.record_event(status, report.signal_id, {"ticket": report.ticket, "message": report.message})
    if not should_notify_execution(status):
        return
    notification = format_execution_notification(status, report, payload)
    if notification:
        send_telegram_message(notification)


def notify_native_event(event: NativeMT5Event) -> bool:
    event_type = str(event.event_type or "").strip().lower()
    q.record_event(
        f"native_{event_type}",
        None,
        {
            "bot_id": event.bot_id,
            "symbol": event.symbol,
            "magic_number": event.magic_number,
            "message": event.message,
        },
    )
    if event_type not in NATIVE_MT5_TELEGRAM_EVENTS:
        return False
    notification = format_native_mt5_event_message(event.model_dump(mode="json", exclude={"secret"}))
    return send_telegram_message(notification) if notification else False


def format_native_mt5_event_message(event: dict) -> str:
    divider = "━━━━━━━━━━━━━━━━━━━━"
    event = event or {}
    event_type = str(event.get("event_type") or "").strip().lower()
    symbol = event.get("symbol") or "n/a"
    side = event.get("side")
    profit = event.get("profit")

    def fmt_price(value):
        if value is None or value == "":
            return "OFF"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    def fmt_money(value, signed=True):
        if value is None or value == "":
            return "n/a"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if not signed:
            return f"{number:.2f} €"
        if number > 0:
            return f"+{number:.2f} €"
        if number < 0:
            return f"{number:.2f} €"
        return "0.00 €"

    def fmt_lot(value):
        if value is None or value == "":
            return "n/a"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    def fmt_side(value):
        normalized = str(value or "").strip().lower()
        if normalized == "buy":
            return "BUY"
        if normalized == "sell":
            return "SELL"
        return str(value or "n/a").upper()

    def side_icon(value):
        normalized = str(value or "").strip().lower()
        if normalized == "buy":
            return "⬆️"
        if normalized == "sell":
            return "⬇️"
        return "⚪"

    def bot_id_pretty(bot_id):
        mapping = {
            "NAS100_ORB_VWAP_RSI_OF": "NAS100 ORB/VWAP",
            "DJ30_ORB_VWAP_RSI_OF": "DJ30 ORB/VWAP",
            "XAUUSD_ORB_VWAP_RSI_OF": "XAUUSD ORB/VWAP",
            "BTCUSD_ORB_VWAP_RSI_OF": "BTCUSD ORB/VWAP",
        }
        return mapping.get(str(bot_id or ""), bot_id or "n/a")

    def fmt_closed_percent(default: str) -> str:
        value = event.get("closed_percent")
        if value is None or value == "":
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if 0 < number <= 1:
            number *= 100
        return f"{number:.0f}%"

    if event_type == "opened":
        return "\n".join(
            [
                "🟢 СДЕЛКА ОТКРЫТА",
                "",
                divider,
                f"🤖 Бот: {bot_id_pretty(event.get('bot_id'))}",
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Направление: {fmt_side(side)}",
                f"📦 Лот: {fmt_lot(event.get('lot'))}",
                divider,
                "",
                f"🎯 Entry: {fmt_price(event.get('entry'))}",
                f"🛡 Stop Loss: {fmt_price(event.get('sl'))}",
                "",
                f"TP1: {fmt_price(event.get('tp1'))}",
                f"TP2: {fmt_price(event.get('tp2'))}",
                f"TP3: {fmt_price(event.get('tp3'))}",
                "",
                divider,
                f"Magic: {event.get('magic_number') or 'n/a'}",
                "⚙️ Режим: Native MT5",
            ]
        )

    if event_type == "tp1_closed":
        return "\n".join(
            [
                "🎯 TP1 ВЗЯТ",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Сделка: {fmt_side(side)}",
                f"✅ Закрыто: {fmt_closed_percent('75%')}",
                "",
                f"Profit: {fmt_money(profit)}",
                "SL переведён в BE",
                divider,
                "",
            ]
        )

    if event_type == "tp2_closed":
        return "\n".join(
            [
                "🎯 TP2 ВЗЯТ",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Сделка: {fmt_side(side)}",
                f"✅ Закрыто: {fmt_closed_percent('25%')}",
                "",
                f"Profit: {fmt_money(profit)}",
                divider,
                "",
            ]
        )

    if event_type == "tp3_closed":
        return "\n".join(
            [
                "🎯 TP3 ВЗЯТ",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Сделка: {fmt_side(side)}",
                "✅ Финальная фиксация",
                "",
                f"Profit: {fmt_money(profit)}",
                divider,
                "",
            ]
        )

    if event_type == "be_moved":
        return "\n".join(
            [
                "🛡 БЕЗУБЫТОК АКТИВИРОВАН",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Сделка: {fmt_side(side)}",
                "",
                "SL перенесён в цену входа:",
                f"BE: {fmt_price(event.get('entry'))}",
                divider,
                "",
                "Теперь риск по сделке = 0",
            ]
        )

    if event_type in {"position_closed", "closed_by_signal"}:
        try:
            profit_value = float(profit or 0)
        except (TypeError, ValueError):
            profit_value = 0.0
        result_icon = "✅" if profit_value > 0 else "🔴" if profit_value < 0 else "⚪"
        return "\n".join(
            [
                f"{result_icon} СДЕЛКА ЗАКРЫТА",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"{side_icon(side)} Сделка: {fmt_side(side)}",
                "",
                f"{result_icon} Итог: {fmt_money(profit)}",
                divider,
                "",
                "Статус: позиция закрыта",
            ]
        )

    if event_type in {"open_failed", "close_failed", "error"}:
        return "\n".join(
            [
                "🔴 ОШИБКА ИСПОЛНЕНИЯ",
                "",
                divider,
                f"📍 Символ: {symbol}",
                f"🤖 Бот: {bot_id_pretty(event.get('bot_id'))}",
                f"⚠️ Событие: {event_type}",
                "",
                "Причина:",
                str(event.get("message") or "n/a"),
                divider,
                "",
                "Проверить:",
                "1. WebRequest",
                "2. AutoTrading",
                "3. Symbol",
                "4. SL/TP distance",
                "5. Минимальный лот",
            ]
        )

    return ""


def format_native_event_notification(event_type: str, event: NativeMT5Event) -> str:
    payload = event.model_dump(mode="json", exclude={"secret"})
    payload["event_type"] = event_type
    return format_native_mt5_event_message(payload)




def format_native_screenshot_caption(event: dict) -> str:
    event = event or {}
    event_type = str(event.get("event_type") or "").strip().lower()
    symbol = event.get("symbol") or "n/a"
    side = fmt_native_side(event.get("side"))

    if event_type == "opened":
        lines = [
            "🟢 СДЕЛКА ОТКРЫТА",
            f"{symbol} | {side} | {fmt_native_lot(event.get('lot'))}",
            "",
            f"Entry: {fmt_native_price(event.get('entry'))}",
            f"SL: {fmt_native_price(event.get('sl'))}",
        ]
        if event.get("tp1") is not None:
            lines.append(f"TP1: {fmt_native_price(event.get('tp1'))}")
        if event.get("tp2") is not None:
            lines.append(f"TP2: {fmt_native_price(event.get('tp2'))}")
        if event.get("tp3") is not None:
            lines.append(f"TP3: {fmt_native_price(event.get('tp3'))}")
        lines.append("Режим: Native MT5")
        return "\n".join(lines)

    if event_type == "tp1_closed":
        return "\n".join(["🎯 TP1 ВЗЯТ", f"{symbol} | {side}", "", "SL переведён в BE"])

    if event_type == "tp2_closed":
        return "\n".join(["🎯 TP2 ВЗЯТ", f"{symbol} | {side}"])

    if event_type == "tp3_closed":
        return "\n".join(["🎯 TP3 ВЗЯТ", f"{symbol} | {side}", "", "Финальная фиксация"])

    if event_type == "be_moved":
        return "\n".join(
            [
                "🛡 БЕЗУБЫТОК АКТИВИРОВАН",
                f"{symbol} | {side}",
                "",
                f"BE: {fmt_native_price(event.get('entry'))}",
                "Риск по сделке = 0",
            ]
        )

    if event_type in {"position_closed", "closed_by_signal"}:
        profit = event.get("profit")
        try:
            profit_value = float(profit or 0)
        except (TypeError, ValueError):
            profit_value = 0.0
        icon = "✅" if profit_value > 0 else "🔴" if profit_value < 0 else "⚪"
        return "\n".join(
            [
                f"{icon} СДЕЛКА ЗАКРЫТА",
                f"{symbol} | {side}",
                "",
                f"Итог: {fmt_native_money(profit, signed=True)}",
            ]
        )

    return str(event.get("caption") or "").strip()[:1024]


def fmt_native_price(value):
    if value is None or value == "":
        return "OFF"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_native_money(value, signed=True):
    if value is None or value == "":
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not signed:
        return f"{number:.2f} €"
    if number > 0:
        return f"+{number:.2f} €"
    if number < 0:
        return f"{number:.2f} €"
    return "0.00 €"


def fmt_native_lot(value):
    if value is None or value == "":
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_native_side(value):
    normalized = str(value or "").strip().lower()
    if normalized == "buy":
        return "BUY"
    if normalized == "sell":
        return "SELL"
    return str(value or "n/a").upper()


def format_execution_notification(status: str, report, payload: Optional[dict]) -> str:
    status = str(status or "").strip().lower()
    payload = payload or {}
    symbol = payload.get("mt5_symbol") or payload.get("symbol") or "нет данных"
    side = payload.get("side") or "нет данных"
    if status in OPEN_EXECUTION_STATUSES:
        return "\n".join(
            [
                "СДЕЛКА ОТКРЫТА",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                f"Сторона: {fmt_side(side)}",
                f"Лот: {fmt_price(payload.get('lot'))}",
                f"Вход: {fmt_price(first_present(report.executed_price, payload.get('entry')))}",
                f"SL: {fmt_price(payload.get('sl'))}",
                "",
                "Цели",
                f"TP1: {fmt_price(payload.get('tp1'))}",
                f"TP2: {fmt_price(payload.get('tp2'))}",
                f"TP3: {fmt_price(payload.get('tp3'))}",
                "",
                "Signal:",
                str(report.signal_id or "нет данных"),
                "",
                fmt_divider(),
            ]
        )
    if status in CLOSE_EXECUTION_STATUSES:
        close_reason = normalize_trade_reason(
            first_present(payload.get("reason"), extract_reason(report.message)),
            status,
        )
        return "\n".join(
            [
                "СДЕЛКА ЗАКРЫТА",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                f"Сторона: {fmt_side(side)}",
                f"Лот: {fmt_price(payload.get('lot'))}",
                "",
                f"Вход: {fmt_price(payload.get('entry'))}",
                f"Выход: {fmt_price(first_present(report.executed_price, payload.get('close_price')))}",
                f"Net PnL: {extract_pnl(report.message)}",
                "",
                f"Причина: {close_reason}",
                f"Ticket: {report.ticket or 'нет данных'}",
                "",
                fmt_divider(),
            ]
        )
    if status in TP_EXECUTION_STATUSES:
        lines = [
            "TAKE PROFIT",
            fmt_divider(),
            "",
            f"Актив: {symbol}",
            f"Цель: {status[:3].upper()}",
        ]
        closed_part = extract_closed_part(report.message)
        if closed_part:
            lines.append(f"Закрыто: {closed_part}")
        lines.extend([f"PnL: {extract_pnl(report.message)}", "", fmt_divider()])
        return "\n".join(lines)
    if status == "be_moved":
        return "\n".join(
            [
                "БЕЗУБЫТОК",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                "SL перенесён в Entry.",
                f"Цена BE: {fmt_price(first_present(payload.get('entry'), report.executed_price))}",
                "",
                fmt_divider(),
            ]
        )
    if status in ERROR_EXECUTION_STATUSES:
        return format_execution_error(report.signal_id, symbol, report.message or "нет данных")
    return ""


def format_execution_report(report: Optional[dict]) -> str:
    if not report:
        return "\n".join(["📡 EXECUTION REPORT", fmt_divider(), "Отчётов исполнения пока нет."])
    return "\n".join(
        [
            "📡 EXECUTION REPORT",
            fmt_divider(),
            "",
            f"Signal: {short_text(report.get('signal_id'), 46)}",
            f"Статус: {str(report.get('status') or 'нет данных').upper()}",
            f"Ticket: {report.get('ticket') or 'нет данных'}",
            f"Цена: {fmt_price(report.get('executed_price'))}",
            f"Время: {format_time(report.get('executed_at'))}",
            f"Сообщение: {short_text(report.get('message') or 'нет данных', 140)}",
            "",
            fmt_divider(),
        ]
    )


def handle_command(text: str, chat_id: Optional[str] = None) -> str:
    stripped = text.strip()
    if not stripped:
        return "Пустая команда."
    stripped = normalize_dashboard_button(stripped)
    if not stripped.startswith("/"):
        return handle_natural_language_command(stripped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID)

    parts = stripped.split()
    command = parts[0].lower()

    if command == "/start":
        return format_start()
    if command == "/status":
        return format_status()
    if command == "/last_trade":
        return format_execution_report(q.last_execution_report())
    if command == "/today":
        return format_today_signals()
    if command == "/account":
        return format_account()
    if command in ("/balance", "/equity"):
        return format_account_short(command.replace("/", ""))
    if command == "/positions":
        return format_positions()
    if command == "/trades":
        return format_trades_today()
    if command == "/pnl_today":
        return format_pnl_today()
    if command == "/history_today":
        return format_history_today()
    if command == "/bots":
        return format_bots()
    if command == "/bot":
        return format_bot_detail(parts[1] if len(parts) > 1 else "")
    if command == "/enable":
        return format_bot_toggle(parts[1] if len(parts) > 1 else "", True)
    if command == "/disable":
        return format_bot_toggle(parts[1] if len(parts) > 1 else "", False)
    if command == "/enable_all":
        return format_all_bots_toggle(True)
    if command == "/disable_all":
        return format_all_bots_toggle(False)
    if command == "/performance":
        return format_performance(parts[1] if len(parts) > 1 else "today")
    if command == "/performance_all":
        return format_performance("all")
    if command == "/symbols":
        return format_symbols()
    if command == "/journal":
        return format_journal(parts[1] if len(parts) > 1 else "today")
    if command == "/trade_last":
        return format_trade_last()
    if command == "/trade":
        return format_trade_detail(parts[1] if len(parts) > 1 else "")
    if command in ("/last_screenshot", "/screenshot"):
        return send_last_screenshot(parts[1] if len(parts) > 1 else "")
    if command == "/bot_settings":
        return format_bot_settings(parts[1] if len(parts) > 1 else "")
    if command == "/daily_report_now":
        return format_daily_report()
    if command == "/news":
        return attach_ai_risk_action_approval(format_market_research(get_market_news_today()), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)
    if command == "/calendar":
        return attach_ai_risk_action_approval(format_market_research(get_economic_calendar_today()), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)
    if command == "/market_today":
        return attach_ai_risk_action_approval(format_market_research(get_market_today_summary()), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)
    if command == "/ask":
        question = stripped[len(parts[0]) :].strip()
        if not question:
            return "Формат: /ask <вопрос>"
        return attach_ai_risk_action_approval(format_ai_answer(answer_with_web_search(question, format_risk())), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)
    if command == "/settings":
        return format_settings(chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
    if command == "/risk":
        return format_risk()
    if command == "/approvals":
        return format_approvals(chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
    if command == "/confirm":
        if len(parts) < 2:
            return "Формат: /confirm <approval_id>"
        ok, message, approval = approve_pending_approval(parts[1], chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
        if ok and approval:
            return "\n".join(["✅ ИЗМЕНЕНИЕ ПРИМЕНЕНО", fmt_divider(), f"Approval ID: {approval['approval_id']}"])
        return format_error("Подтверждение не применено", message)
    if command == "/reject":
        if len(parts) < 2:
            return "Формат: /reject <approval_id>"
        ok, message, approval = reject_pending_approval(parts[1], chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
        if ok and approval:
            return "\n".join(["⛔ ИЗМЕНЕНИЕ ОТКЛОНЕНО", fmt_divider(), f"Approval ID: {approval['approval_id']}"])
        return format_error("Отклонение не применено", message)
    if command == "/pause":
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, {"intent": "pause_trading", "setting_key": "trading_enabled", "operation": "disable", "value": False, "symbol": None})
    if command == "/resume":
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, {"intent": "resume_trading", "setting_key": "trading_enabled", "operation": "enable", "value": True, "symbol": None})
    if command == "/dryrun_on":
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, {"intent": "change_setting", "setting_key": "dry_run", "operation": "enable", "value": True, "symbol": None})
    if command == "/dryrun_off":
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, {"intent": "change_setting", "setting_key": "dry_run", "operation": "disable", "value": False, "symbol": None})
    if command == "/help":
        return format_help()
    return "Команда не распознана. Используй /help или /start."


def dashboard_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "Статус"}, {"text": "Сделки"}],
            [{"text": "⚙️ Управление"}, {"text": "Статистика"}],
            [{"text": "Последний скрин"}, {"text": "⚙️ Настройки"}],
            [{"text": "Новости"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def normalize_dashboard_button(text: str) -> str:
    mapping = {
        "Core Status": "/status",
        "Trade Center": "/trades",
        "Market Intel": "/market_today",
        "Control Panel": "/settings",
        "Statistics": "/performance",
        "Last Screenshot": "/last_screenshot",
        "📊 Core Status": "/status",
        "📈 Trade Center": "/trades",
        "📰 Market Intel": "/market_today",
        "⚙️ Control Panel": "/bots",
        "Статус": "/status",
        "Сделки": "/trades",
        "Новости": "/market_today",
        "Управление": "/bots",
        "⚙️ Управление": "/bots",
        "Статистика": "/performance",
        "Последний скрин": "/last_screenshot",
        "Настройки": "/bot_settings",
        "⚙️ Настройки": "/bot_settings",
        "Назад": "/bots",
        "⬅️ Назад": "/bots",
        "Все боты": "/bots",
    }
    return mapping.get(text, text)


def handle_natural_language_command(text: str, chat_id: str) -> str:
    route = route_plain_text(text)
    if route:
        return route
    symbol_pause = parse_symbol_pause_request(text)
    if symbol_pause:
        return create_change_approval(chat_id, text, symbol_pause)
    market_response = handle_market_language_query(text, chat_id)
    if market_response:
        return market_response

    parsed = parse_natural_language_command(text)
    if parsed.intent == "show_settings":
        return format_settings(chat_id)
    if parsed.intent == "show_status":
        return format_status()
    if parsed.intent == "show_last_trade":
        return format_execution_report(q.last_execution_report())
    if parsed.intent == "unknown" or parsed.confidence < 0.65:
        return format_ai_answer(answer_with_web_search(text, format_risk()))

    parsed_action = parsed.model_dump()
    if parsed.intent == "pause_trading":
        parsed_action.update({"setting_key": "trading_enabled", "operation": "disable", "value": False})
    if parsed.intent == "resume_trading":
        parsed_action.update({"setting_key": "trading_enabled", "operation": "enable", "value": True})
    return create_change_approval(chat_id, text, parsed_action)


def route_plain_text(text: str) -> Optional[str]:
    normalized = text.lower()
    if "сделка" in normalized and any(word in normalized for word in ("закрылась", "закрыта", "почему")):
        return format_execution_report(q.last_execution_report())
    if any(word in normalized for word in ("статус", "сервер", "mt5", "ядро", "core")) and any(word in normalized for word in ("покажи", "что", "как", "сейчас", "состояние")):
        return format_status()
    if any(word in normalized for word in ("баланс", "equity", "счёт", "счет", "аккаунт", "account")):
        return format_account()
    if any(word in normalized for word in ("позиции", "позиция", "открытые", "ордера")):
        return format_positions()
    if any(word in normalized for word in ("сделки", "сделка", "трейды", "история", "pnl", "прибыль")):
        return format_trades_today()
    return None


def handle_market_language_query(text: str, chat_id: str) -> Optional[str]:
    normalized = text.lower()
    if any(phrase in normalized for phrase in ("что сегодня важно", "рынок сегодня", "рыночная сводка", "новости сегодня")):
        return attach_ai_risk_action_approval(format_market_research(get_market_today_summary()), chat_id, text)
    if "календар" in normalized and any(word in normalized for word in ("сегодня", "рын", "эконом")):
        return attach_ai_risk_action_approval(format_market_research(get_economic_calendar_today()), chat_id, text)
    asset = detect_asset_query(normalized)
    if asset and any(marker in normalized for marker in ("влияет", "падает", "растёт", "растет", "движ", "почему", "сегодня", "новости")):
        return attach_ai_risk_action_approval(format_market_research(get_asset_impact_summary(asset)), chat_id, text)
    if any(word in normalized for word in ("новости", "биткоин", "bitcoin", "crypto", "крипт")):
        return attach_ai_risk_action_approval(format_market_research(get_market_news_today()), chat_id, text)
    return None


def attach_ai_risk_action_approval(response: str, chat_id: str, command_text: str) -> str:
    parsed_action = parse_symbol_pause_request(response)
    if not parsed_action:
        return response
    parsed_action["reason"] = "high impact news"
    approval_text = create_change_approval(chat_id, command_text, parsed_action)
    return "\n".join([response.rstrip(), "", "⚠️ AI предложил риск-действие.", "Оно создано как pending approval:", approval_text])


def parse_symbol_pause_request(text: str) -> Optional[dict]:
    normalized = text.lower()
    if not any(word in normalized for word in ("останов", "пауза", "не трог", "не торг", "pause", "stop")):
        return None
    asset = detect_asset_query(normalized)
    if not asset:
        return None
    duration_match = re.search(r"(?:на|for)\s+(\d{1,4})\s*(мин|минут|minutes?|m\b|час|часа|часов|hours?|h\b)", normalized)
    duration_minutes = 30
    if duration_match:
        amount = int(duration_match.group(1))
        unit = duration_match.group(2)
        duration_minutes = amount * 60 if unit.startswith("час") or unit.startswith("hour") or unit == "h" else amount
    paused_until = (datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)).replace(microsecond=0).isoformat()
    return {
        "intent": "pause_symbol",
        "symbol": asset,
        "setting_key": f"symbol_paused_until_{asset}",
        "operation": "set",
        "value": paused_until,
        "duration_minutes": duration_minutes,
        "reason": "telegram risk action request",
    }


def detect_asset_query(normalized: str) -> Optional[str]:
    aliases = {
        "xauusd": "XAUUSD",
        "золото": "XAUUSD",
        "gold": "XAUUSD",
        "nas100": "NAS100",
        "nasdaq": "NAS100",
        "dj30": "DJ30",
        "dow": "DJ30",
        "us500": "US500",
        "sp500": "US500",
        "s&p": "US500",
        "btc": "BTCUSD",
        "btcusd": "BTCUSD",
        "биткоин": "BTCUSD",
        "bitcoin": "BTCUSD",
    }
    for alias, asset in aliases.items():
        if alias in normalized:
            return asset
    return None


def create_change_approval(chat_id: str, command_text: str, parsed_action: dict) -> str:
    setting_key = parsed_action.get("setting_key")
    operation = parsed_action.get("operation")
    symbol = parsed_action.get("symbol")
    value = parsed_action.get("value")
    old_value = get_setting(setting_key) if setting_key else None
    new_value = calculate_new_value(old_value, operation, value)
    validation_error = validate_change(setting_key, new_value, symbol)
    if validation_error:
        return format_error("Команда отклонена риск-контролем", validation_error)
    parsed_action["setting_key"] = setting_key
    parsed_action["new_value"] = new_value
    approval = create_pending_approval(chat_id, command_text, parsed_action, old_value, new_value)
    if setting_key == "dry_run" and new_value is False and allow_real_trading():
        return "\n".join(
            [
                "⚠️ REAL TRADING UNLOCK",
                fmt_divider(),
                "",
                "Ты пытаешься выключить DryRun на REAL account.",
                f"Approval ID: {approval['approval_id']}",
                f"Для подтверждения: /confirm {approval['approval_id']}",
                "",
                f"Для отмены: /reject {approval['approval_id']}",
                "",
                fmt_divider(),
            ]
        )
    return "\n".join(
        [
            "🧾 PENDING APPROVAL",
            fmt_divider(),
            "",
            f"Параметр: {setting_key}",
            f"Сейчас: {old_value}",
            f"Новое: {new_value}",
            f"Approval ID: {approval['approval_id']}",
            "",
            "Применить:",
            f"/confirm {approval['approval_id']}",
            "",
            "Отклонить:",
            f"/reject {approval['approval_id']}",
            "",
            fmt_divider(),
        ]
    )


def calculate_new_value(old_value, operation: Optional[str], value):
    if operation == "enable":
        return True
    if operation == "disable":
        return False
    if operation == "increase_percent":
        return round(float(old_value or 0) * (1 + float(value)), 6)
    if operation == "decrease_percent":
        return round(float(old_value or 0) * (1 - float(value)), 6)
    return value


def validate_change(setting_key: Optional[str], new_value, symbol: Optional[str]) -> Optional[str]:
    if not setting_key:
        return "Не найден параметр изменения."
    if setting_key not in KNOWN_SETTING_KEYS:
        return f"Неизвестный параметр: {setting_key}."
    allowed = [item.strip() for item in str(get_setting("allowed_symbols", "XAUUSD,NAS100,DJ30,US500,BTCUSD")).split(",") if item.strip()]
    if symbol and symbol not in allowed:
        return f"Символ не разрешён: {symbol}."
    if setting_key.startswith("symbol_lot_multiplier_"):
        setting_symbol = setting_key.replace("symbol_lot_multiplier_", "")
        if setting_symbol not in SYMBOLS or setting_symbol not in allowed:
            return f"Символ не разрешён: {setting_symbol}."
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "Lot multiplier должен быть числом."
        if numeric_value <= 0:
            return "Lot multiplier должен быть больше 0."
        if numeric_value > 3.0:
            return "Lot multiplier не может быть выше 3.0."
    if setting_key.startswith("symbol_paused_until_"):
        setting_symbol = setting_key.replace("symbol_paused_until_", "")
        if setting_symbol not in SYMBOLS or setting_symbol not in allowed:
            return f"Символ не разрешён: {setting_symbol}."
        if new_value:
            try:
                datetime.fromisoformat(str(new_value))
            except ValueError:
                return "Время паузы должно быть ISO datetime."
    if setting_key == "global_lot_multiplier":
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "global_lot_multiplier должен быть числом."
        if numeric_value <= 0:
            return "global_lot_multiplier должен быть больше 0."
        if numeric_value > 3.0:
            return "global_lot_multiplier не может быть выше 3.0."
    if setting_key == "max_lot":
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "max_lot должен быть числом."
        if numeric_value <= 0:
            return "max_lot должен быть больше 0."
        if numeric_value > 1.0:
            return "max_lot не может быть выше 1.0 в demo-first режиме."
    if setting_key == "dry_run" and new_value is False and latest_account_is_real() and not allow_real_trading():
        return "dry_run=false заблокирован в demo-first режиме. Live trading из Telegram не включается."
    if setting_key in ("dry_run", "use_server_lot") and not isinstance(new_value, bool):
        return f"{setting_key} должен быть boolean."
    if setting_key == "trading_enabled" and not isinstance(new_value, bool):
        return "trading_enabled должен быть boolean."
    if setting_key in ("max_daily_loss", "max_trades_per_day"):
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return f"{setting_key} должен быть числом."
        if numeric_value < 0:
            return f"{setting_key} не может быть отрицательным."
    if setting_key == "allowed_symbols":
        requested = [item.strip() for item in str(new_value).split(",") if item.strip()]
        unknown = [item for item in requested if item not in SYMBOLS]
        if unknown:
            return f"Неизвестные символы запрещены: {', '.join(unknown)}."
    return None


def format_start() -> str:
    return "\n".join(
        [
            "🤖 AI TRADING CONTROL",
            fmt_divider(),
            "",
            "Система управления активна.",
            "",
            "Выбери раздел ниже:",
            "",
            "📡 Статус",
            "Сервер, MT5, баланс, equity",
            "",
            "💼 Сделки",
            "Позиции, история, PnL",
            "",
            "🧠 Новости",
            "Рынок, календарь, риск",
            "",
            "⚙️ Управление",
            "Настройки, пауза, подтверждения",
            "",
            fmt_divider(),
            f"Активы: {format_assets_line()}",
        ]
    )


def format_status() -> str:
    account = acct.latest_account_snapshot()
    if config.is_native_mt5_only() and not account:
        return NATIVE_NO_DATA_MESSAGE
    counts = q.command_counts()
    if config.is_native_mt5_only():
        counts = {"queued": 0, "sent": 0, "acknowledged": 0}
    positions = acct.current_positions()
    pnl = acct.pnl_today()
    currency = account_currency(account)
    heartbeat = acct.last_mt5_heartbeat()
    closed_pnl = pnl.get("closed_pnl", pnl.get("net_pnl"))
    total_pnl = pnl.get("total_pnl", pnl.get("net_pnl"))
    lines = [
        "TRADING CONTROL AGENT",
        fmt_divider(),
        "",
        "Режим: Native MT5" if config.is_native_mt5_only() else f"Режим: {config.SYSTEM_MODE}",
        f"Последний MT5 сигнал: {format_heartbeat(heartbeat)}",
        f"Команд в очереди: {counts.get('queued', 0)}",
        "",
        "АККАУНТ",
        f"Баланс: {fmt_money(account.get('balance') if account else None, currency)}",
        f"Equity: {fmt_money(account.get('equity') if account else None, currency)}",
        f"Margin: {fmt_money(account.get('margin') if account else None, currency)}",
        f"Free margin: {fmt_money(account.get('free_margin') if account else None, currency)}",
        "",
        "PnL сегодня",
        f"Закрытый: {fmt_pnl_or_unavailable(closed_pnl, currency, pnl.get('realized_available', True))}",
        f"Плавающий: {fmt_pnl(pnl.get('floating_pnl'), currency)}",
        f"Итого: {fmt_pnl(total_pnl, currency)}",
        "",
        "Позиции",
        f"Открытых: {account_open_positions(account, positions)}",
        "",
        "Активы",
        format_assets_line(),
    ]
    if account and is_real_trade_mode(account.get("trade_mode")):
        lines.extend(["", "REAL ACCOUNT DETECTED", "Проверить риск перед торговлей."])
    lines.extend(["", fmt_divider(), f"Обновлено: {berlin_now()} Berlin"])
    return "\n".join(lines)


def format_account() -> str:
    account = acct.latest_account_snapshot()
    if not account:
        if config.is_native_mt5_only():
            return NATIVE_NO_DATA_MESSAGE
        return "\n".join(
            [
                "💠 ACCOUNT MATRIX",
                fmt_divider(),
                "Данные MT5 пока не получены.",
                "",
                "Проверь:",
                "1. EA запущен",
                "2. Algo Trading включён",
                "3. WebRequest разрешён",
                "4. Сервер Render онлайн",
            ]
        )
    currency = account_currency(account)
    positions = acct.current_positions()
    lines = [
        "АККАУНТ MT5",
        "",
        f"Баланс: {fmt_money(account.get('balance'), currency)}",
        f"Equity: {fmt_money(account.get('equity'), currency)}",
        f"Margin: {fmt_money(account.get('margin'), currency)}",
        f"Free margin: {fmt_money(account.get('free_margin'), currency)}",
        f"Открытых позиций: {account_open_positions(account, positions)}",
        f"Последнее обновление: {format_heartbeat(account.get('created_at') or account.get('snapshot_at'))}",
    ]
    if is_real_trade_mode(account.get("trade_mode")):
        lines.extend(["", "REAL ACCOUNT DETECTED", "Проверить риск перед торговлей."])
    return "\n".join(lines)


def format_account_short(key: str) -> str:
    account = acct.latest_account_snapshot()
    if not account:
        return format_account()
    currency = account_currency(account)
    label = "Баланс" if key == "balance" else "Equity"
    return "\n".join(["АККАУНТ MT5", "", f"{label}: {fmt_money(account.get(key), currency)}"])


def format_positions() -> str:
    positions = acct.current_positions()
    if not positions:
        if config.is_native_mt5_only() and not acct.native_data_available():
            return NATIVE_NO_DATA_MESSAGE
        return "\n".join(["ОТКРЫТЫХ ПОЗИЦИЙ НЕТ", "", "Новых активных позиций нет."])
    account = acct.latest_account_snapshot() or {}
    currency = account_currency(account)
    total = 0.0
    lines = ["ОТКРЫТЫЕ ПОЗИЦИИ", ""]
    for index, position in enumerate(positions[:10], start=1):
        total += float_or_zero(position.get("profit"))
        if index > 1:
            lines.extend(["", THIN_DIVIDER, ""])
        prefix = f"{position.get('symbol') or 'нет данных'} {fmt_side(position.get('side'))} {fmt_lot(position.get('lot'))}"
        lines.extend(
            [
                prefix,
                f"Entry: {fmt_price(first_present(position.get('entry'), position.get('entry_price')))}",
                f"SL: {fmt_price(position.get('sl'))}",
                f"TP1: {fmt_price(position.get('tp1'))}",
                f"TP2: {fmt_price(position.get('tp2'))}",
                f"TP3: {fmt_price(position.get('tp3'))}",
                f"BE: {yes_no(position.get('be_done'))}",
                f"PnL: {fmt_pnl(position.get('profit'), currency)}",
            ]
        )
    if len(positions) > 10:
        lines.append(f"Ещё позиций: {len(positions) - 10}")
    lines.extend(["", f"Floating PnL: {fmt_pnl(total, currency)}"])
    return "\n".join(lines)


def format_trades_today() -> str:
    positions = acct.current_positions()
    trades = acct.trades_today()
    if not positions and not trades:
        if config.is_native_mt5_only() and not acct.native_data_available():
            return NATIVE_NO_DATA_MESSAGE
        return "СЕГОДНЯ СДЕЛОК НЕТ"
    account = acct.latest_account_snapshot() or {}
    currency = account_currency(account)
    total = 0.0
    lines: list[str] = []
    if positions:
        lines.extend(["ОТКРЫТЫЕ ПОЗИЦИИ", ""])
        for index, position in enumerate(positions[:10], start=1):
            if index > 1:
                lines.extend(["", THIN_DIVIDER, ""])
            lines.extend(
                [
                    f"{position.get('symbol') or 'нет данных'} {fmt_side(position.get('side'))} {fmt_lot(position.get('lot'))}",
                    f"Entry: {fmt_price(first_present(position.get('entry'), position.get('entry_price')))}",
                    f"SL: {fmt_price(position.get('sl'))}",
                    f"TP1: {fmt_price(position.get('tp1'))}",
                    f"TP2: {fmt_price(position.get('tp2'))}",
                    f"BE: {yes_no(position.get('be_done'))}",
                    f"PnL: {fmt_pnl(position.get('profit'), currency)}",
                ]
            )
        if len(positions) > 10:
            lines.append(f"Ещё позиций: {len(positions) - 10}")
    if trades:
        if lines:
            lines.extend(["", fmt_divider(), ""])
        lines.extend(["✅ ЗАКРЫТЫЕ СДЕЛКИ СЕГОДНЯ", ""])
    for index, trade in enumerate(trades[:10], start=1):
        total += float_or_zero(trade.get("net_profit"))
        if index > 1:
            lines.extend(["", THIN_DIVIDER, ""])
        lines.extend(
            [
                f"{trade.get('symbol') or 'нет данных'} {fmt_side(trade.get('side'))}",
                f"Profit: {fmt_pnl(trade.get('net_profit'), currency)}",
                f"Закрыта: {format_time(trade.get('closed_at') or trade.get('created_at'))}",
            ]
        )
    if len(trades) > 10:
        lines.append(f"Ещё сделок: {len(trades) - 10}")
    if trades:
        lines.extend(["", f"Итог дня: {fmt_pnl(total, currency)}"])
    return "\n".join(lines)


def format_pnl_today() -> str:
    if config.is_native_mt5_only() and not acct.native_data_available():
        return NATIVE_NO_DATA_MESSAGE
    summary = acct.pnl_today()
    account = acct.latest_account_snapshot() or {}
    currency = account_currency(account)
    closed_pnl = summary.get("closed_pnl", summary.get("net_pnl"))
    total_pnl = summary.get("total_pnl", summary.get("net_pnl"))
    lines = [
        "PnL СЕГОДНЯ",
        "",
        f"Закрытый PnL: {fmt_pnl_or_unavailable(closed_pnl, currency, summary.get('realized_available', True))}",
        f"Плавающий PnL: {fmt_pnl(summary.get('floating_pnl'), currency)}",
        f"Итого: {fmt_pnl(total_pnl, currency)}",
        "",
        f"Сделок закрыто: {summary.get('closed_trades_count', summary.get('trades_count', 0))}",
        f"TP событий: {summary.get('tp_events', 0)}",
        f"Ошибок исполнения: {summary.get('execution_errors', 0)}",
    ]
    return "\n".join(lines)


def format_bots() -> str:
    controls = acct.list_native_bot_controls(include_defaults=True)
    if not controls:
        return NATIVE_NO_DATA_MESSAGE
    lines = ["NATIVE MT5 BOTS", ""]
    for control in controls:
        position = control.get("active_position")
        position_text = "нет"
        if position:
            position_text = f"{fmt_side(position.get('side'))} {fmt_lot(position.get('lot'))}"
        lines.extend(
            [
                bot_display(control),
                f"Статус: {control.get('online_status') or 'offline'}",
                f"Trading: {'enabled' if control_enabled(control) else 'disabled'}",
                f"Heartbeat: {format_heartbeat(control.get('last_heartbeat_at'))}",
                f"Позиция: {position_text}",
                "",
            ]
        )
    lines.extend(["Команды:", "/bot NAS100", "/enable NAS100", "/disable NAS100", "/performance 7d", "/journal today"])
    return "\n".join(lines).rstrip()


def format_bot_detail(selector: str) -> str:
    control = acct.get_native_bot_control(selector)
    if not control:
        return "Бот не найден. Используй /bots или /bot NAS100."
    position = control.get("active_position")
    position_text = "none"
    if position:
        position_text = f"{fmt_side(position.get('side'))} {fmt_lot(position.get('lot'))}"
    perf = acct.performance_summary("today", control.get("bot_id"))
    total = perf.get("totals", {})
    lines = [
        bot_display(control),
        "",
        f"Статус: {'Включён' if control_enabled(control) else 'Остановлен'}",
        f"Символ: {control.get('symbol') or 'нет данных'}",
        f"Magic: {control.get('magic_number') or 'нет данных'}",
        f"Heartbeat: {format_heartbeat(control.get('last_heartbeat_at'))}",
        f"Последнее событие: {control.get('last_event_type') or 'none'}",
        f"Открытая позиция: {position_text}",
        f"PnL сегодня: {fmt_pnl(total.get('total_pnl'), '€')}",
        "",
        "Кнопки:",
        "▶️ Включить бота",
        "⏸ Остановить бота",
        "Статистика",
        "Последний скрин",
        "⚙️ Настройки",
        "⬅️ Назад",
        "",
        f"Команды: /enable {control.get('asset')} | /disable {control.get('asset')}",
    ]
    return "\n".join(lines)


def format_bot_toggle(selector: str, enabled: bool) -> str:
    if not selector:
        return "Формат: /enable NAS100 или /disable NAS100"
    control = acct.set_native_bot_enabled(selector, enabled, "Telegram control")
    if not control:
        return "Бот не найден. Используй /bots."
    if enabled:
        return "\n".join(
            [
                "▶️ БОТ ВКЛЮЧЁН",
                "",
                f"Бот: {bot_display(control)}",
                f"Символ: {control.get('symbol') or 'нет данных'}",
                "Новые сделки разрешены.",
            ]
        )
    return "\n".join(
        [
            "⏸ БОТ ОСТАНОВЛЕН",
            "",
            f"Бот: {bot_display(control)}",
            f"Символ: {control.get('symbol') or 'нет данных'}",
            "Новые сделки запрещены.",
            "Если позиция уже открыта, сопровождение TP/SL/BE продолжается.",
        ]
    )


def format_all_bots_toggle(enabled: bool) -> str:
    controls = acct.set_all_native_bots_enabled(enabled, "Telegram control")
    state = "включены" if enabled else "остановлены"
    return "\n".join([f"Все native MT5 боты {state}.", "", f"Ботов: {len(controls)}", "Открытые позиции не закрываются."])


def format_symbols() -> str:
    controls = acct.list_native_bot_controls(include_defaults=True)
    lines = ["АКТИВЫ", ""]
    for control in controls:
        lines.append(f"{control.get('asset')}: {control.get('symbol')} | {control.get('bot_id')}")
    return "\n".join(lines)


def format_performance(arg: str = "today") -> str:
    period, selector = parse_performance_arg(arg)
    summary = acct.performance_summary(period, selector)
    label = period_label(period)
    items = summary.get("items", [])
    if not items:
        return "\n".join(["PERFORMANCE CONTROL", "", f"Период: {label}", "", "Данных пока нет."])
    lines = ["PERFORMANCE CONTROL", "", f"Период: {label}", ""]
    for item in items:
        lines.extend(
            [
                item.get("symbol") or "UNKNOWN",
                f"Trades: {item.get('trades_count', 0)}",
                f"Winrate: {item.get('winrate', 0):.1f}%",
                f"Closed PnL: {fmt_pnl(item.get('closed_pnl'), '€')}",
                f"Floating: {fmt_pnl(item.get('floating_pnl'), '€')}",
                f"Total: {fmt_pnl(item.get('total_pnl'), '€')}",
                f"PF: {fmt_pf(item.get('profit_factor'))}",
                f"Best: {fmt_pnl(item.get('best_trade'), '€')}",
                f"Worst: {fmt_pnl(item.get('worst_trade'), '€')}",
                f"TP1: {item.get('tp1_count', 0)} | TP2: {item.get('tp2_count', 0)} | BE: {item.get('be_count', 0)}",
                f"Errors: {(item.get('open_failed_count', 0) or 0) + (item.get('close_failed_count', 0) or 0)}",
                f"Status: {item.get('status')}",
                "",
            ]
        )
    totals = summary.get("totals", {})
    lines.extend(
        [
            "ИТОГО:",
            f"Trades: {totals.get('trades_count', 0)}",
            f"Closed PnL: {fmt_pnl(totals.get('closed_pnl'), '€')}",
            f"Floating PnL: {fmt_pnl(totals.get('floating_pnl'), '€')}",
            f"Total PnL: {fmt_pnl(totals.get('total_pnl'), '€')}",
        ]
    )
    return "\n".join(lines)


def format_journal(arg: str = "today") -> str:
    period = parse_period_arg(arg)
    entries = acct.journal_entries(period, None, limit=30)
    today = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
    if not entries:
        return "\n".join(["ЖУРНАЛ СДЕЛОК", "", f"Сегодня: {today}", "", "Сделок нет."])
    total = 0.0
    wins = 0
    closed = 0
    lines = ["ЖУРНАЛ СДЕЛОК", "", f"Сегодня: {today}" if period == "today" else f"Период: {period_label(period)}", ""]
    for index, trade in enumerate(entries, start=1):
        profit = trade.get("profit")
        if trade.get("closed_at"):
            closed += 1
            total += float_or_zero(profit)
            wins += 1 if float_or_zero(profit) > 0 else 0
        lines.extend(
            [
                f"{index}. {trade.get('symbol') or 'нет данных'} {fmt_side(trade.get('side'))} {fmt_lot(trade.get('lot'))}",
                f"Entry: {fmt_price(trade.get('entry'))}",
                f"Result: {fmt_pnl(profit, '€')}",
                f"TP1: {checkmark(trade.get('tp1_done'))} TP2: {checkmark(trade.get('tp2_done'))} BE: {checkmark(trade.get('be_done'))}",
                f"Открыта: {format_time(trade.get('opened_at'))}",
                f"Закрыта: {format_time(trade.get('closed_at')) if trade.get('closed_at') else trade.get('status') or 'open'}",
                "",
            ]
        )
    winrate = round((wins / closed) * 100, 1) if closed else 0.0
    lines.extend(["ИТОГ ДНЯ:", f"Сделок: {closed}", f"Winrate: {winrate:.1f}%", f"Closed PnL: {fmt_pnl(total, '€')}"])
    return "\n".join(lines)


def format_trade_last() -> str:
    trade = acct.last_journal_trade()
    if not trade:
        return "Сделок пока нет."
    screenshot = acct.last_native_screenshot(trade.get("symbol"))
    if screenshot:
        send_telegram_photo(screenshot.get("file_path"), screenshot.get("caption") or "Последний скрин")
    return format_trade_record(trade)


def format_trade_detail(trade_id: str) -> str:
    if not trade_id:
        return "Формат: /trade <id>"
    trade = acct.get_journal_trade(trade_id)
    if not trade:
        return "Сделка не найдена."
    return format_trade_record(trade)


def format_trade_record(trade: dict) -> str:
    return "\n".join(
        [
            "СДЕЛКА",
            "",
            f"ID: {trade.get('id')}",
            f"UID: {trade.get('trade_uid')}",
            f"Бот: {trade.get('bot_id') or 'нет данных'}",
            f"Символ: {trade.get('symbol') or 'нет данных'}",
            f"Сторона: {fmt_side(trade.get('side'))}",
            f"Лот: {fmt_lot(trade.get('lot'))}",
            f"Entry: {fmt_price(trade.get('entry'))}",
            f"SL: {fmt_price(trade.get('sl'))}",
            f"TP1: {fmt_price(trade.get('tp1'))}",
            f"TP2: {fmt_price(trade.get('tp2'))}",
            f"TP3: {fmt_price(trade.get('tp3'))}",
            f"TP1: {checkmark(trade.get('tp1_done'))} TP2: {checkmark(trade.get('tp2_done'))} TP3: {checkmark(trade.get('tp3_done'))} BE: {checkmark(trade.get('be_done'))}",
            f"Статус: {trade.get('status') or 'нет данных'}",
            f"Причина закрытия: {trade.get('close_reason') or 'нет данных'}",
            f"Profit: {fmt_pnl(trade.get('profit'), '€')}",
            f"Открыта: {format_time(trade.get('opened_at'))}",
            f"Закрыта: {format_time(trade.get('closed_at'))}",
        ]
    )


def send_last_screenshot(selector: str = "") -> str:
    screenshot = acct.last_native_screenshot(selector or None)
    label = selector.upper() if selector else "ботам"
    if not screenshot:
        return f"Скриншотов по {label} пока нет."
    sent = send_telegram_photo(screenshot.get("file_path"), screenshot.get("caption") or "Последний скрин")
    if not sent:
        return "Скрин найден, но Telegram sendPhoto не прошёл."
    return "Последний скрин отправлен."


def format_bot_settings(selector: str = "") -> str:
    control = acct.get_native_bot_control(selector) if selector else None
    if not control:
        controls = acct.list_native_bot_controls(include_defaults=True)
        control = controls[0] if controls else None
    if not control:
        return "EA settings unavailable. Будут добавлены после следующего heartbeat."
    summary = decode_settings_summary(control.get("settings_summary"))
    lines = [
        "⚙️ НАСТРОЙКИ БОТА",
        "",
        f"Бот: {bot_display(control)}",
        f"Символ: {control.get('symbol') or 'нет данных'}",
        f"Magic: {control.get('magic_number') or 'нет данных'}",
        f"Статус: {'Включён' if control_enabled(control) else 'Остановлен'}",
        "",
        "Remote control: ON",
        "Notifications: ON",
        "Screenshots: ON",
        "Account snapshot: every 1 min",
        "",
    ]
    if not summary:
        lines.append("EA settings unavailable. Будут добавлены после следующего heartbeat.")
    else:
        lines.extend(
            [
                f"ORB: {summary.get('orb_window', 'unavailable')}",
                f"Mode: {summary.get('mode', summary.get('retest', 'unavailable'))}",
                f"VWAP filter: {on_off(summary.get('vwap_filter'))}",
                f"TP: {summary.get('tp_mode', 'unavailable')}",
                f"TP1/TP2/TP3: {summary.get('tp1', 'n/a')} / {summary.get('tp2', 'n/a')} / {summary.get('tp3', 'n/a')}",
                f"BE after TP1: {on_off(summary.get('be_enabled'))}",
            ]
        )
    return "\n".join(lines)


def format_daily_report() -> str:
    if not acct.native_data_available():
        return "\n".join(["DAILY REPORT", "Сегодня данных от native MT5 bots пока нет."])
    account = acct.latest_account_snapshot() or {}
    pnl = acct.pnl_today()
    perf = acct.performance_summary("today")
    totals = perf.get("totals", {})
    items = perf.get("items", [])
    best = max(items, key=lambda item: item.get("closed_pnl", 0), default=None)
    worst = min(items, key=lambda item: item.get("closed_pnl", 0), default=None)
    controls = acct.list_native_bot_controls(include_defaults=True)
    lines = [
        "DAILY REPORT",
        "Время: 21:00 Berlin",
        f"Дата: {datetime.now(BERLIN_TZ).strftime('%Y-%m-%d')}",
        "",
        fmt_divider(),
        "АККАУНТ",
        f"Баланс: {fmt_money(account.get('balance'), account_currency(account))}",
        f"Equity: {fmt_money(account.get('equity'), account_currency(account))}",
        f"Margin: {fmt_money(account.get('margin'), account_currency(account))}",
        f"Free margin: {fmt_money(account.get('free_margin'), account_currency(account))}",
        "",
        fmt_divider(),
        "PnL ДНЯ",
        f"Closed PnL: {fmt_pnl(pnl.get('closed_pnl'), account_currency(account))}",
        f"Floating PnL: {fmt_pnl(pnl.get('floating_pnl'), account_currency(account))}",
        f"Total PnL: {fmt_pnl(pnl.get('total_pnl'), account_currency(account))}",
        "",
        fmt_divider(),
        "СДЕЛКИ",
        f"Всего: {totals.get('trades_count', 0)}",
        f"Побед: {totals.get('wins', 0)}",
        f"Убытков: {totals.get('losses', 0)}",
        f"Winrate: {totals.get('winrate', 0):.1f}%",
        "",
        f"TP1: {totals.get('tp1_count', 0)}",
        f"TP2: {totals.get('tp2_count', 0)}",
        f"TP3: {totals.get('tp3_count', 0)}",
        f"BE: {totals.get('be_count', 0)}",
        f"Ошибок исполнения: {(totals.get('open_failed_count', 0) or 0) + (totals.get('close_failed_count', 0) or 0)}",
        "",
        fmt_divider(),
        "Лучший актив:",
        f"{best.get('symbol')} {fmt_pnl(best.get('closed_pnl'), account_currency(account))}" if best else "нет данных",
        "",
        "⚠️ Худший актив:",
        f"{worst.get('symbol')} {fmt_pnl(worst.get('closed_pnl'), account_currency(account))}" if worst else "нет данных",
        "",
        fmt_divider(),
        "БОТЫ",
    ]
    for control in controls:
        lines.append(f"{control.get('asset')}: {control.get('online_status')}")
    return "\n".join(lines)


def format_history_today() -> str:
    if config.is_native_mt5_only() and not acct.native_data_available():
        return NATIVE_NO_DATA_MESSAGE
    summary = acct.pnl_today()
    trades = acct.trades_today()
    by_symbol: dict[str, float] = {}
    for trade in trades:
        symbol = trade.get("symbol") or "UNKNOWN"
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + float_or_zero(trade.get("net_profit"))
    trades_count = summary.get("trades_count") or 0
    wins = summary.get("wins") or 0
    losses = summary.get("losses") or 0
    winrate = round((wins / trades_count) * 100, 1) if trades_count else 0.0
    account = acct.latest_account_snapshot() or {}
    currency = account_currency(account)
    lines = [
        "📊 СТАТИСТИКА ДНЯ",
        fmt_divider(),
        "",
        f"Сделок: {trades_count}",
        f"Плюсовых: {wins}",
        f"Минусовых: {losses}",
        f"Winrate: {winrate:.1f}%",
        "",
        f"Net PnL: {fmt_pnl(summary.get('net_pnl'), currency)}",
    ]
    if summary.get("best_trade") is not None:
        lines.append(f"Лучшая: {fmt_pnl(summary.get('best_trade'), currency)}")
    if summary.get("worst_trade") is not None:
        lines.append(f"Худшая: {fmt_pnl(summary.get('worst_trade'), currency)}")
    if by_symbol:
        lines.extend(["", "Активы:"])
        for symbol in ("NAS100", "XAUUSD", "BTCUSD", "US500", "DJ30"):
            if symbol in by_symbol:
                lines.append(f"{symbol}: {fmt_pnl(by_symbol[symbol], '')}")
    lines.extend(["", fmt_divider()])
    return "\n".join(lines)


def format_today_signals() -> str:
    summary = q.today_summary()
    return "\n".join(["📡 СИГНАЛЫ СЕГОДНЯ", fmt_divider(), "", f"Сигналов: {summary.get('signals', 0)}", f"Открыто: {summary.get('opened', 0)}", f"Отклонено: {summary.get('rejected', 0)}", f"PnL оценка: {fmt_money(summary.get('estimated_pnl'), 'USD')}"])


def format_settings(chat_id: Optional[str] = None) -> str:
    approvals = list_pending_approvals(chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
    trading_enabled = bool(get_setting("trading_enabled", config.TRADING_ENABLED))
    dry_run = bool(get_setting("dry_run", True))
    return "\n".join(
        [
            "⚙️ ПАНЕЛЬ УПРАВЛЕНИЯ",
            fmt_divider(),
            "",
            f"Торговля:  {'ENABLED' if trading_enabled else '⏸ PAUSED'}",
            f"DryRun:  {'ON' if dry_run else '⚪ OFF'}",
            f"Real unlock: {'ENABLED' if allow_real_trading() else 'DISABLED'}",
            f"Подтверждения: {len(approvals)}",
            "",
            fmt_section("РИСК"),
            f"Global lot multiplier: {get_setting('global_lot_multiplier')}",
            f"Max lot: {get_setting('max_lot')}",
            f"Max trades/day: {get_setting('max_trades_per_day')}",
            f"Max daily loss: {get_setting('max_daily_loss')}",
            "",
            "Команды:",
            "/pause — остановить торговлю",
            "/resume — включить торговлю",
            "/dryrun_on — включить DryRun",
            "/dryrun_off — выключить DryRun",
            "/approvals — подтверждения",
            "/confirm <id> — применить",
            "/reject <id> — отклонить",
            "",
            fmt_divider(),
            "Изменения риска только через подтверждение.",
        ]
    )


def format_risk() -> str:
    keys = [
        "trading_enabled",
        "dry_run",
        "use_server_lot",
        "global_lot_multiplier",
        "max_lot",
        "max_daily_loss",
        "max_trades_per_day",
        "allowed_symbols",
        "symbol_lot_multiplier_XAUUSD",
        "symbol_lot_multiplier_NAS100",
        "symbol_lot_multiplier_DJ30",
        "symbol_lot_multiplier_US500",
        "symbol_lot_multiplier_BTCUSD",
        "symbol_paused_until_XAUUSD",
        "symbol_paused_until_NAS100",
        "symbol_paused_until_DJ30",
        "symbol_paused_until_US500",
        "symbol_paused_until_BTCUSD",
    ]
    lines = ["🛡 RISK MATRIX", fmt_divider(), ""]
    for key in keys:
        lines.append(f"{key}: {get_setting(key)}")
    return "\n".join(lines)


def format_approvals(chat_id: str) -> str:
    approvals = list_pending_approvals(chat_id)
    if not approvals:
        return "\n".join(["✅ ПОДТВЕРЖДЕНИЙ НЕТ", fmt_divider(), "Очередь risk changes пуста."])
    lines = ["🧾 PENDING APPROVALS", fmt_divider(), ""]
    for index, approval in enumerate(approvals[:8], start=1):
        parsed = json.loads(approval["parsed_action"])
        if index > 1:
            lines.extend(["", THIN_DIVIDER, ""])
        lines.extend([f"{index}. ID: {approval['approval_id']}", f"Параметр: {parsed.get('setting_key')}", f"Сейчас: {approval['old_value']}", f"Новое: {approval['new_value']}", f"Истекает: {format_time(approval['expires_at'])}"])
    return "\n".join(lines)


def format_help() -> str:
    return "\n".join(["🤖 AI TRADING CONTROL", fmt_divider(), "", "/start — главное меню", "/status — ядро системы", "/account — счёт MT5", "/positions — открытые позиции", "/trades — сделки сегодня", "/history_today — статистика дня", "/news — новости", "/calendar — календарь", "/market_today — рынок сегодня", "/ask <вопрос> — AI вопрос", "/settings — управление", "/approvals — подтверждения", "", "Можно писать словами:", "что сегодня важно по рынку", "почему NAS100 падает сегодня", "покажи статус", "", fmt_divider()])


def format_market_research(response: str) -> str:
    cleaned = clean_ai_text(response)
    if is_web_timeout(cleaned):
        return "\n".join(["🧠 РЫНОЧНАЯ СВОДКА", fmt_divider(), "", "AI web search долго отвечает.", "Повтори через минуту или спроси через /ask."])
    sources = short_sources(cleaned)
    body = normalize_market_headings(remove_source_blocks(cleaned))
    lines = ["🧠 РЫНОЧНАЯ СВОДКА", fmt_divider(), ""]
    useful_lines = [line for line in body.splitlines() if line.strip()] or ["Данных пока недостаточно."]
    lines.extend(useful_lines[:32])
    if sources:
        lines.extend(["", fmt_section("ИСТОЧНИКИ")])
        lines.extend(f"{index}. {source}" for index, source in enumerate(sources[:5], start=1))
    lines.extend(["", fmt_divider()])
    return "\n".join(lines)[:3900]


def format_ai_answer(response: str) -> str:
    cleaned = clean_ai_text(response)
    if is_web_timeout(cleaned):
        return "\n".join(["🧠 AI CORE", fmt_divider(), "", "AI web search долго отвечает.", "Повтори через минуту или спроси через /ask."])
    sources = short_sources(cleaned)
    body = remove_source_blocks(cleaned)
    lines = ["🧠 AI CORE", fmt_divider(), ""]
    lines.extend([line for line in body.splitlines() if line.strip()][:24] or ["Нет подтверждённых данных."])
    if sources:
        lines.extend(["", fmt_section("ИСТОЧНИКИ")])
        lines.extend(f"{index}. {source}" for index, source in enumerate(sources[:5], start=1))
    lines.extend(["", fmt_divider()])
    return "\n".join(lines)[:3900]


def format_notification(event_type: str, signal_id: Optional[str], details: Optional[str]) -> str:
    status = event_type.lower()
    if status == "open_failed":
        return format_execution_error(signal_id, "нет данных", details or "нет данных")
    return "\n".join(["📡 СИСТЕМНОЕ СОБЫТИЕ", fmt_divider(), "", f"Тип: {event_type}", f"Signal: {short_text(signal_id, 54)}", f"Детали: {short_text(details or 'нет данных', 180)}"])


def format_execution_error(signal_id: Optional[str], symbol: str, error: str) -> str:
    return "\n".join(
        [
            "ОШИБКА ИСПОЛНЕНИЯ",
            fmt_divider(),
            "",
            f"Актив: {symbol or 'нет данных'}",
            f"Signal: {short_text(signal_id, 120)}",
            f"Ошибка: {short_text(error, 180)}",
            "",
            "Проверить:",
            "1. MT5 запущен",
            "2. Algo Trading включён",
            "3. Символ есть у брокера",
            "4. Лот допустим",
            "5. WebRequest разрешён",
            "",
            fmt_divider(),
        ]
    )


def format_native_bot_id(bot_id: Optional[str]) -> str:
    if not bot_id:
        return "нет данных"
    return str(bot_id).replace("_", " ")


def is_be_sl(event: NativeMT5Event) -> bool:
    try:
        if event.sl is not None and event.entry is not None and abs(float(event.sl) - float(event.entry)) < 0.00001:
            return True
    except (TypeError, ValueError):
        pass
    return "be" in str(event.message or "").lower()


def fmt_fixed(value) -> str:
    if value is None or value == "":
        return "нет данных"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_closed_percent(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 < number <= 1:
        number *= 100
    return f"{number:.0f}%"


def fmt_money(value, currency: str = "USD") -> str:
    if value is None:
        return "нет данных"
    try:
        amount = f"{float(value):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        amount = str(value)
    return f"{amount} {currency}".strip()


def fmt_pnl(value, currency: str = "USD") -> str:
    if value is None:
        return "нет данных"
    try:
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.2f} {currency}".strip()
    except (TypeError, ValueError):
        return str(value)


def fmt_pnl_or_unavailable(value, currency: str = "USD", available: bool = True) -> str:
    if not available:
        return "realized unavailable"
    return fmt_pnl(value, currency)


def fmt_lot(value) -> str:
    if value is None or value == "":
        return "нет данных"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_price(value) -> str:
    if value is None or value == "":
        return "нет данных"
    try:
        return f"{float(value):.5f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def fmt_status_dot(enabled) -> str:
    return "🟢" if enabled else "🔴"


def fmt_section(title) -> str:
    return f"▌ {title}"


def fmt_divider() -> str:
    return DIVIDER


def mask_login(login) -> str:
    if not login:
        return "нет данных"
    text = str(login)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def short_sources(text_or_sources) -> list[str]:
    if isinstance(text_or_sources, list):
        raw_lines = [str(item) for item in text_or_sources]
    else:
        text = str(text_or_sources or "")
        source_block = re.split(r"(?im)^\s*(?:sources|источники)\s*:?\s*$", text)
        raw_lines = source_block[-1].splitlines() if len(source_block) > 1 else []
    sources: list[str] = []
    for line in raw_lines:
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        line = re.sub(r"https?://\S+", "", line).strip(" -—")
        if line and line.lower() not in {"sources", "источники"} and line not in sources:
            sources.append(re.sub(r"\s+", " ", line)[:48])
    return sources[:5]


def format_trade_mode(trade_mode) -> str:
    if trade_mode is None:
        return "нет данных"
    normalized = str(trade_mode).lower()
    if normalized in ("0", "demo"):
        return "DEMO"
    if normalized in ("1", "real", "live"):
        return "REAL"
    return str(trade_mode)


def is_real_trade_mode(trade_mode) -> bool:
    return format_trade_mode(trade_mode).lower() == "real"


def latest_account_is_real() -> bool:
    account = acct.latest_account_snapshot()
    return bool(account and is_real_trade_mode(account.get("trade_mode")))


def format_heartbeat(value) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "нет данных" if not value else str(value)
    seconds = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return f"{seconds} сек назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    return f"{minutes // 60} ч назад"


def format_time(value) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "нет данных" if not value else str(value)
    return parsed.astimezone(BERLIN_TZ).strftime("%H:%M")


def parse_datetime(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def berlin_now() -> str:
    return datetime.now(BERLIN_TZ).strftime("%H:%M")


def fmt_percent(value) -> str:
    if value is None:
        return "нет данных"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return str(value)


def fmt_side(value) -> str:
    normalized = str(value or "").lower()
    if normalized == "buy":
        return "BUY"
    if normalized == "sell":
        return "SELL"
    return str(value or "нет данных").upper()


def float_or_zero(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def account_currency(account: Optional[dict]) -> str:
    currency = str((account or {}).get("currency") or "").strip()
    if not currency and config.is_native_mt5_only():
        return "€"
    if currency.upper() == "EUR":
        return "€"
    return currency or "USD"


def account_open_positions(account: Optional[dict], positions: list[dict]) -> int:
    value = (account or {}).get("open_positions")
    try:
        return int(value)
    except (TypeError, ValueError):
        return len(positions)


def format_assets_line() -> str:
    if config.is_native_mt5_only():
        return " · ".join(acct.native_assets()) or DEFAULT_ASSETS_LINE
    return DEFAULT_ASSETS_LINE


def yes_no(value) -> str:
    return "YES" if bool(value) else "NO"


def control_enabled(control: dict) -> bool:
    return bool(int(control.get("enabled", 1) or 0))


def bot_display(control: dict) -> str:
    return control.get("display_name") or str(control.get("bot_id") or control.get("symbol") or "native bot")


def parse_period_arg(value: str) -> str:
    normalized = str(value or "today").strip().lower()
    if normalized in ("7d", "7", "week"):
        return "7d"
    if normalized in ("30d", "30", "month"):
        return "30d"
    if normalized in ("all", "alltime"):
        return "all"
    return "today"


def parse_performance_arg(value: str) -> tuple[str, Optional[str]]:
    normalized = str(value or "today").strip()
    lower = normalized.lower()
    if lower in ("today", "7d", "7", "30d", "30", "all", "alltime"):
        return parse_period_arg(normalized), None
    return "today", normalized


def period_label(period: str) -> str:
    return {
        "today": "сегодня",
        "7d": "7 дней",
        "30d": "30 дней",
        "all": "all",
    }.get(period, period)


def fmt_pf(value) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number >= 999:
        return "∞"
    return f"{number:.2f}"


def checkmark(value) -> str:
    return "✅" if bool(value) else "❌"


def decode_settings_summary(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def on_off(value) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return "ON"
        if normalized in ("0", "false", "no", "off"):
            return "OFF"
        return value
    return "ON" if bool(value) else "OFF"


def short_text(value, limit: int) -> str:
    text = str(value or "нет данных").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clean_ai_text(text: str) -> str:
    text = str(text or "").replace("\r", "")
    text = re.sub(r"\[[^\]]+\]\(https?://[^)]+\)", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = text.replace("Market News Today", "").strip()
    return re.sub(r"[ \t]{2,}", " ", text)


def remove_source_blocks(text: str) -> str:
    return re.split(r"(?im)^\s*(?:sources|источники)\s*:?\s*$", text)[0].strip()


def normalize_market_headings(text: str) -> str:
    for old, new in {
        "Вывод:": "⚡ ВЫВОД",
        "События:": "▌ СОБЫТИЯ",
        "Риск по активам:": "▌ РИСК ПО АКТИВАМ",
        "AI-комментарий:": "▌ AI-КОММЕНТАРИЙ",
        "Комментарий:": "▌ AI-КОММЕНТАРИЙ",
    }.items():
        text = text.replace(old, new)
    return text


def is_web_timeout(text: str) -> bool:
    lowered = text.lower()
    return "web search долго отвечает" in lowered or "timed out" in lowered


def extract_pnl(message: Optional[str]) -> str:
    text = str(message or "")
    patterns = [
        r"(?:net\s*)?pnl\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)",
        r"(?:net_profit|profit)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)",
        r"([+-]\d+(?:\.\d+)?)\s*(?:usd|pnl)",
        r"(?:usd)\s*([+-]?\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return fmt_pnl(match.group(1), "USD")
    return "нет данных"


def extract_closed_part(message: Optional[str]) -> Optional[str]:
    text = str(message or "")
    percent_match = re.search(r"close_percent\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%?", text, re.I)
    if percent_match:
        return f"{percent_match.group(1)}%"
    percent_match = re.search(r"(?:closed|close)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%", text, re.I)
    if percent_match:
        return f"{percent_match.group(1)}%"
    percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent_match:
        return f"{percent_match.group(1)}%"
    volume_match = re.search(r"(?:volume|closed_volume|close_volume)\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.I)
    if volume_match:
        return volume_match.group(1)
    return None


def extract_reason(message: Optional[str]) -> Optional[str]:
    text = str(message or "")
    reason_match = re.search(r"(?:reason|причина)\s*[:=]\s*([^,;|\n]+)", text, re.I)
    if reason_match:
        return reason_match.group(1).strip()
    lowered = text.lower()
    for token, reason in {
        "tp1_closed": "tp1_closed",
        "tp2_closed": "tp2_closed",
        "tp3_closed": "tp3_closed",
        "closed_by_signal": "closed_by_signal",
        "close signal": "close signal",
        "manual": "manual",
        "stop loss": "SL",
        "sl": "SL",
    }.items():
        if token in lowered:
            return reason
    return None


def normalize_trade_reason(reason, fallback_status: str) -> str:
    normalized = str(reason or fallback_status or "").strip()
    mapping = {
        "tp1_closed": "TP1",
        "tp2_closed": "TP2",
        "tp3_closed": "TP3",
        "be_moved": "BE",
        "closed_by_signal": "close signal",
        "dry_run_close": "dry run close",
        "position_closed": "final TP",
        "open_failed": "ошибка открытия",
        "close_failed": "ошибка закрытия",
    }
    return mapping.get(normalized.lower(), normalized or "нет данных")


def format_error(title: str, details: str) -> str:
    return "\n".join(["⚠️ " + title.upper(), fmt_divider(), "", short_text(details, 400)])


def parse_telegram_update(update: dict) -> tuple[Optional[str], Optional[str]]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return (str(chat_id) if chat_id is not None else None, text)
