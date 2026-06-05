import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import account_store as acct
from . import bias_store
from . import signal_store
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
from .native_trade_notifications import clean_mode_enabled, format_clean_trade_message, normalizeNativeTradeEvent
from .live_bias_engine import format_live_bias_telegram_message
from .signal_accuracy import evaluate_signal_accuracy
from .settings_store import (
    approve_pending_approval,
    create_pending_approval,
    get_setting,
    list_pending_approvals,
    reject_pending_approval,
)
from .database import db

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
    try:
        from telegram import WebAppInfo
    except Exception:
        WebAppInfo = None
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
except Exception:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    KeyboardButton = None
    ReplyKeyboardMarkup = None
    WebAppInfo = None
    CallbackQueryHandler = None
    CommandHandler = None
    MessageHandler = None
    filters = None


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
BE_SUPPRESS_AFTER_TP1_SECONDS = 300
recent_tp1_symbols: set[str] = set()
_BE_SUPPRESS_AFTER_TP1_TIMES: dict[str, datetime] = {}

OPEN_EXECUTION_STATUSES = {"opened", "dry_run_open"}
TP_EXECUTION_STATUSES = {"tp1_closed", "tp2_closed", "tp3_closed"}
CLOSE_EXECUTION_STATUSES = {"position_closed", "closed_by_signal", "dry_run_close"}
ERROR_EXECUTION_STATUSES = {"open_failed", "close_failed", "rejected", "close_rejected"}
MENU_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
MENU_BOTS = [
    "NAS100_ORB_VWAP_RSI_OF",
    "SP500_ORB_VWAP_RSI_OF",
    "DJ30_ORB_VWAP_RSI_OF",
    "BTCUSD_ORB_VWAP_RSI_OF",
    "GER40_ORB_VWAP_RSI_OF",
]
MENU_ASSETS = ["NAS100", "SP500", "DJ30", "BTCUSD", "GER40"]
MARKET_SYMBOLS = {
    "NAS100": "^IXIC",
    "SP500": "^GSPC",
    "DJ30": "^DJI",
    "BTCUSD": "BTC-USD",
    "GER40": "^GDAXI",
    "VIX": "^VIX",
}
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://tv-smob-server-1.onrender.com/dashboard")
ACCOUNT_POSITIONS_BUTTON_TEXT = "📊 Счёт и позиции"
BIAS_BUTTON_TEXT = "📈 Байес"
SIGNALS_BUTTON_TEXT = "⚡ Сигналы"
TRADES_BUTTON_TEXT = "🧾 Сделки"
ANALYTICS_BUTTON_TEXT = "📉 Аналитика"
SITE_BUTTON_TEXT = "🌐 Открыть SMOB"


def _keyboard_button_payload(text: str):
    if text == SITE_BUTTON_TEXT:
        return {"text": text, "web_app": {"url": DASHBOARD_URL}}
    return {"text": text}


def _reply_keyboard_button(text: str):
    if text == SITE_BUTTON_TEXT and WebAppInfo:
        try:
            return KeyboardButton(text, web_app=WebAppInfo(url=DASHBOARD_URL))
        except TypeError:
            pass
    return KeyboardButton(text)


MAIN_KEYBOARD_ROWS = [
    [ACCOUNT_POSITIONS_BUTTON_TEXT],
    [BIAS_BUTTON_TEXT, SIGNALS_BUTTON_TEXT],
    [TRADES_BUTTON_TEXT, ANALYTICS_BUTTON_TEXT],
    [SITE_BUTTON_TEXT],
]
if ReplyKeyboardMarkup and KeyboardButton:
    MAIN_KEYBOARD = ReplyKeyboardMarkup(
        [[_reply_keyboard_button(text) for text in row] for row in MAIN_KEYBOARD_ROWS],
        resize_keyboard=True,
        is_persistent=True,
    )
else:
    MAIN_KEYBOARD = {
        "keyboard": [[_keyboard_button_payload(text) for text in row] for row in MAIN_KEYBOARD_ROWS],
        "resize_keyboard": True,
        "is_persistent": True,
    }
MENU_BUTTON_CALLBACKS = {
    ACCOUNT_POSITIONS_BUTTON_TEXT: "refresh_account_positions",
    BIAS_BUTTON_TEXT: "refresh_bias",
    SIGNALS_BUTTON_TEXT: "refresh_signals",
    TRADES_BUTTON_TEXT: "refresh_processed_trades",
    ANALYTICS_BUTTON_TEXT: "refresh_analytics",
}
user_state = {}

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


def send_telegram_message(text: str, reply_markup: Optional[dict] = None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    data = urllib.parse.urlencode(payload).encode("utf-8")
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


def _notification_symbol(value) -> str:
    return str(value or "").strip().upper()


def _mark_tp1_be_suppressed(symbol) -> None:
    normalized = _notification_symbol(symbol)
    if normalized:
        recent_tp1_symbols.add(normalized)
        _BE_SUPPRESS_AFTER_TP1_TIMES[normalized] = datetime.now(timezone.utc)


def _is_be_suppressed_after_tp1(symbol) -> bool:
    normalized = _notification_symbol(symbol)
    if not normalized:
        return False
    if normalized in recent_tp1_symbols:
        recent_tp1_symbols.discard(normalized)
        _BE_SUPPRESS_AFTER_TP1_TIMES.pop(normalized, None)
        return True
    marked_at = _BE_SUPPRESS_AFTER_TP1_TIMES.get(normalized)
    if not marked_at:
        return False
    age = (datetime.now(timezone.utc) - marked_at).total_seconds()
    if age <= BE_SUPPRESS_AFTER_TP1_SECONDS:
        return True
    _BE_SUPPRESS_AFTER_TP1_TIMES.pop(normalized, None)
    return False


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
    symbol = (payload or {}).get("mt5_symbol") or (payload or {}).get("symbol")
    if status == "be_moved" and _is_be_suppressed_after_tp1(symbol):
        return
    notification = format_execution_notification(status, report, payload)
    if notification:
        sent = send_telegram_message(notification)
        if sent and status == "tp1_closed":
            _mark_tp1_be_suppressed(symbol)


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
    payload = event.model_dump(mode="json", exclude={"secret"})
    if event_type == "be_moved" and _is_be_suppressed_after_tp1(event.symbol):
        return False
    # Enrich with DB trade data so format function has accumulated profit, timestamps, etc.
    trade = acct.get_trade_for_notification(event)
    if trade:
        if event_type == "be_moved" and trade.get("tp1_done"):
            return False
        if event_type in {"position_closed", "closed_by_signal"}:
            # Use accumulated total profit (TP1+TP2+close), not just remaining lot
            if trade.get("profit") is not None:
                payload["total_profit"] = trade["profit"]
            payload.setdefault("opened_at", trade.get("opened_at"))
            payload.setdefault("exit_price", trade.get("exit_price"))
            payload.setdefault("entry", trade.get("entry"))
            payload.setdefault("sl", trade.get("sl"))
            payload.setdefault("lot", trade.get("lot"))
            payload["tp1_done"] = bool(trade.get("tp1_done"))
            payload["tp2_done"] = bool(trade.get("tp2_done"))
            payload["tp3_done"] = bool(trade.get("tp3_done"))
            payload["be_done"] = bool(trade.get("be_done"))
            if trade.get("tp1_profit") is not None:
                payload["tp1_profit"] = trade["tp1_profit"]
            if trade.get("tp2_profit") is not None:
                payload["tp2_profit"] = trade["tp2_profit"]
        elif event_type in {"tp1_closed", "tp2_closed", "tp3_closed"}:
            # Keep event.profit as this TP's individual profit
            # Add accumulated running total from the now-updated active trade
            payload["accumulated_profit"] = trade.get("profit")
            payload.setdefault("entry", trade.get("entry"))
            payload.setdefault("sl", trade.get("sl"))
            payload.setdefault("lot", trade.get("lot"))
            if trade.get("tp1_profit") is not None:
                payload["tp1_profit"] = trade["tp1_profit"]
            if trade.get("tp2_profit") is not None:
                payload["tp2_profit"] = trade["tp2_profit"]
    # Native event delivery is coordinated in main.py so the screenshot endpoint
    # can merge the formatted text into a single photo caption.
    return False


def format_native_mt5_event_message(event: dict) -> str:
    event = event or {}
    normalized = normalizeNativeTradeEvent(event)
    if clean_mode_enabled():
        if not normalized["shouldNotifyTelegram"]:
            return ""
        try:
            daily_stats = acct.native_pnl_today() if normalized["telegramTemplate"] == "closed" else None
        except Exception:
            daily_stats = None
        return format_clean_trade_message(event, normalized, daily_stats)
    event_type = str(event.get("event_type") or "").strip().lower()
    symbol = event.get("symbol") or "n/a"
    side = fmt_native_side(event.get("side"))

    def num(value) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def first_num(*keys) -> Optional[float]:
        for key in keys:
            value = num(event.get(key))
            if value is not None:
                return value
        return None

    def positive(value) -> bool:
        return value is not None and value > 0

    def price(value) -> Optional[str]:
        value = num(value)
        return f"{value:.2f}" if value is not None else None

    def money(value, *, signed: bool = True, absolute: bool = False) -> Optional[str]:
        value = num(value)
        if value is None:
            return None
        if absolute:
            value = abs(value)
        if signed:
            sign = "+" if value > 0 else "-" if value < 0 else ""
            return f"{sign}€{abs(value):.2f}"
        return f"€{value:.2f}"

    def r_value(profit_value, risk_value) -> Optional[float]:
        if profit_value is None or not positive(risk_value):
            return None
        try:
            return profit_value / risk_value
        except ZeroDivisionError:
            return None

    def r_text(value) -> Optional[str]:
        value = num(value)
        if value is None:
            return None
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}R"

    def pts(entry_value, level_value) -> Optional[str]:
        entry_num = num(entry_value)
        level_num = num(level_value)
        if entry_num is None or level_num is None:
            return None
        return f"+{abs(level_num - entry_num):.1f} pts"

    def line_with_price(label: str, value, entry_value=None) -> Optional[str]:
        text = price(value)
        if text is None:
            return None
        point_text = pts(entry_value, value) if entry_value is not None else None
        prefix = f"{label}:".ljust(7)
        return f"{prefix} {text}  ({point_text})" if point_text else f"{prefix} {text}"

    def timestamp(time_str=None) -> str:
        parsed = parse_datetime(time_str)
        if parsed:
            return "🕐 " + parsed.astimezone(BERLIN_TZ).strftime("%H:%M  %d.%m.%Y")
        return "🕐 " + datetime.now(BERLIN_TZ).strftime("%H:%M  %d.%m.%Y")

    def duration_minutes(opened_at, closed_at) -> Optional[int]:
        opened = parse_datetime(opened_at)
        closed = parse_datetime(closed_at) or datetime.now(timezone.utc)
        if not opened:
            return None
        return max(0, int((closed.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60))

    entry = first_present(event.get("entry"), event.get("entry_price"))
    exit_price = first_present(event.get("exit_price"), event.get("current_price"), event.get("close_price"))
    lot = num(event.get("lot"))
    risk_money = first_num("risk_money", "risk", "initial_risk", "risk_amount")
    profit_money = first_num("profit_money", "profit")
    tp1_profit = first_num("tp1_profit")
    total_profit_payload = first_num("total_profit", "accumulated_profit")

    if event_type == "opened":
        def tp_points(value):
            target = num(value)
            base = num(entry)
            if target is None or base is None:
                return None
            return f"{abs(target - base):.1f}"

        def opened_tp_block(label: str, target, expected, detail: str) -> list[str]:
            target_price = price(target)
            if not target_price:
                return []
            expected_value = money(expected) if positive(expected) else ""
            first_line = f"{label}: {target_price}"
            if expected_value:
                first_line += f"    {expected_value}"
            points = tp_points(target)
            details = [detail]
            if points:
                details.append(f"{points} pts")
            return [first_line, f"     ({', '.join(details)})", ""]

        tp1_expected = first_num("tp1_expected")
        tp2_expected = first_num("tp2_expected")
        tp3_expected = first_num("tp3_expected")
        max_profit = sum(value for value in (tp1_expected, tp2_expected, tp3_expected) if positive(value))
        lines = ["🟢 СДЕЛКА ОТКРЫТА", ""]
        header = [f"📊 {symbol}", side]
        opened_lot = first_num("lot", "lots")
        if opened_lot is not None:
            header.append(f"{opened_lot:.2f} lot")
        lines.extend(["  |  ".join(header), ""])
        if price(entry):
            lines.append(f"Entry:  {price(entry)}")
        if price(event.get("sl")):
            lines.append(f"SL:     {price(event.get('sl'))}")
        lines.extend(["", "", "🎯 ТЕЙКИ И РАСЧЁТ", ""])
        lines.extend(opened_tp_block("TP1", event.get("tp1"), tp1_expected, "75% позиции"))
        if positive(num(event.get("tp2"))):
            lines.extend(opened_tp_block("TP2", event.get("tp2"), tp2_expected, "25% позиции"))
        if positive(num(event.get("tp3"))):
            lines.extend(opened_tp_block("TP3", event.get("tp3"), tp3_expected, "остаток"))
        money_lines = []
        if positive(risk_money):
            money_lines.append(f"💰 Риск:          {money(risk_money, signed=False)}")
        if positive(max_profit):
            money_lines.extend([f"🏆 Макс прибыль:  {money(max_profit)}", "   (если все TP)"])
        if money_lines:
            lines.extend(["", *money_lines])
        lines.extend(["", "", timestamp(event.get("time"))])
        return "\n".join(lines)

    if event_type == "tp1_closed":
        profit_r = first_num("profit_r")
        lines = ["🎯 TP1 ВЗЯТ", "", f"📊 {symbol}  |  {side}", ""]
        tp_price = first_present(event.get("tp1"), event.get("exit_price"), event.get("current_price"))
        if price(tp_price):
            lines.append(f"TP1:  {price(tp_price)}")
        lines.extend(["Закрыто: 75% позиции", ""])
        if profit_money is not None:
            lines.append(f"💰 Зафиксировано:  {money(profit_money)}")
        if profit_r is not None:
            lines.append(f"📊 R:               {r_text(profit_r)}")
        lines.extend(["", "🛡 SL  BE", "Остаток в рынке: 25%", "", timestamp(event.get("time"))])
        return "\n".join(lines)

    if event_type == "tp2_closed":
        total_so_far = total_profit_payload
        if total_so_far is None and tp1_profit is not None and profit_money is not None:
            total_so_far = tp1_profit + profit_money
        cumulative_r = r_value(total_so_far, risk_money)
        lines = ["🎯🎯 TP2 ВЗЯТ", "", f"📊 {symbol}  |  {side}", ""]
        tp_price = first_present(event.get("tp2"), event.get("exit_price"), event.get("current_price"))
        if price(tp_price):
            lines.extend([f"TP2:  {price(tp_price)}", ""])
        if profit_money is not None:
            lines.append(f"💰 Эта часть:   {money(profit_money)}")
        if total_so_far is not None:
            lines.append(f"💰 Суммарно:    {money(total_so_far)}")
        if cumulative_r is not None:
            lines.append(f"📊 R суммарно:  {r_text(cumulative_r)}")
        lines.extend(["", timestamp(event.get("time"))])
        return "\n".join(lines)

    if event_type == "be_moved":
        lines = ["🛡 БЕЗУБЫТОК АКТИВИРОВАН", "", f"📊 {symbol}  |  {side}"]
        if price(entry):
            lines.append(f"BE: {price(entry)}")
        lines.extend(["", timestamp(event.get("time"))])
        return "\n".join(lines)

    if event_type in {"closed", "position_closed", "closed_by_signal"}:
        total_profit = total_profit_payload
        if total_profit is None and tp1_profit is not None and profit_money is not None:
            total_profit = tp1_profit + profit_money
        if total_profit is None:
            total_profit = profit_money or 0.0
        win = total_profit > 0
        header_parts = [f"📊 {symbol}", side]
        if lot is not None:
            header_parts.append(f"{lot:.2f} lot")
        title = "СДЕЛКА ЗАКРЫТА  ПРОФИТ" if win else "СДЕЛКА ЗАКРЫТА  УБЫТОК"
        lines = [title, "", "  |  ".join(header_parts), ""]
        if price(entry):
            lines.append(f"Entry:  {price(entry)}")
        if price(exit_price):
            lines.append(f"Exit:   {price(exit_price)}")
        if win:
            lines.append("")
            if positive(tp1_profit):
                lines.append(f"TP1:  {money(tp1_profit)}")
            tp2_profit = first_num("tp2_profit")
            if event.get("tp2_done") and positive(tp2_profit):
                lines.append(f"TP2:  {money(tp2_profit)}")
            elif event.get("tp2_done") and positive(profit_money):
                lines.append(f"TP2:  {money(profit_money)}")
            lines.extend(["", f"💰 Итого:   {money(total_profit)}"])
        else:
            lines.extend(["SL сработал", "", f"💰 Убыток:  {money(total_profit, signed=False, absolute=True)}"])
        total_r = r_value(total_profit, risk_money)
        if total_r is not None:
            lines.append(f"📊 R:        {r_text(total_r)}")
        duration = duration_minutes(event.get("opened_at"), event.get("time"))
        if duration is not None:
            lines.append(f"Время:    {duration} мин")
        try:
            daily = acct.native_pnl_today()
            day_pnl = money(daily.get("closed_pnl"))
            if day_pnl:
                lines.extend(["", f"📅 День: {day_pnl}  |  {daily.get('wins', 0)}W / {daily.get('losses', 0)}L"])
        except Exception:
            pass
        lines.extend(["", timestamp(event.get("time"))])
        return "\n".join(lines)

    if event_type in {"open_failed", "close_failed", "rejected", "close_rejected", "error"}:
        reason = event.get("message") or event.get("reason")
        lines = ["⚠️ ВХОД НЕ ВЫПОЛНЕН"]
        if reason:
            lines.append(f"Причина: {reason}")
        lines.extend(["", timestamp(event.get("time"))])
        return "\n".join(lines)

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

    def _num(v) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _money(v) -> str:
        number = _num(v) or 0.0
        sign = "+" if number > 0 else "-" if number < 0 else ""
        return f"{sign}€{abs(number):.2f}"

    def _r_text(v) -> str:
        number = _num(v) or 0.0
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.2f}R"

    if event_type == "opened":
        risk = _num(first_present(event.get("risk_money"), event.get("risk"), event.get("initial_risk"), event.get("risk_amount")))
        if risk and risk > 0:
            return " | ".join([symbol, side, f"Risk: -{_money(risk).lstrip('+')}"])
        return " | ".join([symbol, side, "opened"])

    if event_type == "tp1_closed":
        profit = _num(first_present(event.get("profit_money"), event.get("profit")))
        profit_r = _num(event.get("profit_r"))
        parts = [symbol, side]
        if profit is not None:
            parts.append(f"TP1 {_money(profit)}")
        if profit_r is not None:
            parts.append(_r_text(profit_r))
        return " | ".join(parts)

    profit = None
    if event_type in {"closed", "position_closed", "closed_by_signal"}:
        profit = _num(first_present(event.get("total_profit"), event.get("accumulated_profit")))
    if profit is None:
        profit = _num(event.get("profit_money"))
    if profit is None:
        profit = _num(event.get("profit"))

    r_value = _num(first_present(event.get("r_total"), event.get("profit_r")))
    if r_value is None:
        risk = _num(first_present(event.get("risk_money"), event.get("risk"), event.get("initial_risk"), event.get("risk_amount")))
        if profit is not None and risk and risk > 0:
            r_value = profit / risk

    if profit is not None or r_value is not None:
        return " | ".join([symbol, side, _money(profit), _r_text(r_value)])

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


def ptb_reply_markup(markup):
    if not isinstance(markup, dict):
        return markup
    if InlineKeyboardMarkup and InlineKeyboardButton and "inline_keyboard" in markup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        btn.get("text", ""),
                        url=btn.get("url"),
                        callback_data=btn.get("callback_data") if not btn.get("url") else None,
                    )
                    for btn in row
                ]
                for row in markup.get("inline_keyboard", [])
            ]
        )
    if ReplyKeyboardMarkup and KeyboardButton and "keyboard" in markup:
        def make_button(btn):
            if isinstance(btn, str):
                return KeyboardButton(btn)
            text = btn.get("text", "")
            web_app = btn.get("web_app") or {}
            if web_app.get("url") and WebAppInfo:
                try:
                    return KeyboardButton(text, web_app=WebAppInfo(url=web_app["url"]))
                except TypeError:
                    pass
            return KeyboardButton(text)

        return ReplyKeyboardMarkup(
            [[make_button(btn) for btn in row] for row in markup.get("keyboard", [])],
            resize_keyboard=bool(markup.get("resize_keyboard", True)),
            is_persistent=bool(markup.get("is_persistent", True)),
        )
    return markup


async def _ptb_callback_router(update, context):
    query = getattr(update, "callback_query", None)
    if query:
        await query.answer()
        chat_id = str(query.message.chat_id) if getattr(query, "message", None) else ""
        text, keyboard = render_menu_callback(str(query.data or ""), chat_id)
        try:
            await query.edit_message_text(text, reply_markup=ptb_reply_markup(keyboard))
        except Exception:
            if getattr(query, "message", None):
                await query.message.reply_text(text, reply_markup=ptb_reply_markup(keyboard))


menu_callback_query_handler = CallbackQueryHandler(_ptb_callback_router) if CallbackQueryHandler else None


async def start(update, context):
    if getattr(update, "message", None):
        await update.message.reply_text(menu_main_text(), reply_markup=ptb_reply_markup(MAIN_KEYBOARD))


async def _reply_menu_section(update, callback_data: str) -> None:
    if not getattr(update, "message", None):
        return
    chat_id = str(update.message.chat_id)
    text, keyboard = render_menu_callback(callback_data, chat_id)
    await update.message.reply_text(text, reply_markup=ptb_reply_markup(keyboard))


async def show_status(update, context):
    await _reply_menu_section(update, "menu_status")


async def show_trades_menu(update, context):
    await _reply_menu_section(update, "menu_trades")


async def show_stats_menu(update, context):
    await _reply_menu_section(update, "menu_stats")


async def show_actions_menu(update, context):
    await _reply_menu_section(update, "menu_actions")


async def show_market(update, context):
    await _reply_menu_section(update, "menu_market")


async def show_records(update, context):
    await _reply_menu_section(update, "menu_records")


async def show_risk(update, context):
    await _reply_menu_section(update, "menu_risk")


async def show_bots(update, context):
    await _reply_menu_section(update, "menu_bots")


async def show_site(update, context):
    if getattr(update, "message", None):
        await update.message.reply_text(site_message(), reply_markup=ptb_reply_markup(site_inline_keyboard()))


async def handle_menu_button(update, context):
    text = update.message.text
    if text in MENU_BUTTON_CALLBACKS:
        chat_id = getattr(update.message, "chat_id", None)
        if chat_id is None and getattr(update, "effective_chat", None):
            chat_id = update.effective_chat.id
        rendered_text, keyboard = render_menu_callback(MENU_BUTTON_CALLBACKS[text], str(chat_id or ""))
        await update.message.reply_text(rendered_text, reply_markup=ptb_reply_markup(keyboard))
    elif text in (SITE_BUTTON_TEXT, "Сайт"):
        await show_site(update, context)


MENU_BUTTON_PATTERN = r"^(📊 Счёт и позиции|📈 Байес|⚡ Сигналы|🧾 Сделки|📉 Аналитика|🌐 Открыть SMOB|Сайт)$"
menu_message_handler = (
    MessageHandler(filters.TEXT & filters.Regex(MENU_BUTTON_PATTERN), handle_menu_button)
    if MessageHandler and filters
    else None
)
start_command_handler = CommandHandler(["start", "menu"], start) if CommandHandler else None
site_command_handler = CommandHandler(["site", "dashboard"], show_site) if CommandHandler else None


def register_menu_handlers(app) -> None:
    if menu_message_handler:
        app.add_handler(menu_message_handler, group=-1)
    if start_command_handler:
        app.add_handler(start_command_handler, group=-1)
    if site_command_handler:
        app.add_handler(site_command_handler, group=-1)
    if menu_callback_query_handler:
        app.add_handler(menu_callback_query_handler)


def menu_timestamp() -> str:
    return "🕐 " + datetime.now(BERLIN_TZ).strftime("%H:%M  %d.%m.%Y")


def safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def menu_money(value, currency: str = "$", decimals: int = 2, signed: bool = False) -> Optional[str]:
    number = safe_float(value)
    if number is None:
        return None
    sign = "+" if signed and number >= 0 else "-" if signed and number < 0 else ""
    return f"{sign}{currency}{abs(number):,.{decimals}f}"


def menu_number(value, decimals: int = 2, signed: bool = False) -> Optional[str]:
    number = safe_float(value)
    if number is None:
        return None
    sign = "+" if signed and number >= 0 else "-" if signed and number < 0 else ""
    return f"{sign}{abs(number):,.{decimals}f}"


def menu_duration(start, end) -> Optional[str]:
    opened = parse_datetime(start)
    closed = parse_datetime(end)
    if not opened or not closed:
        return None
    minutes = max(0, int((closed.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() // 60))
    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f"{hours}ч {mins}м" if mins else f"{hours}ч 0м"
    return f"{minutes}м"


def format_minutes(value) -> Optional[str]:
    minutes_value = safe_float(value)
    if minutes_value is None:
        return None
    minutes = max(0, int(minutes_value))
    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f"{hours}ч {mins}м" if mins else f"{hours}ч 0м"
    return f"{minutes}м"


def menu_asset_from_trade(row: dict) -> str:
    text = str(row.get("symbol") or row.get("bot_id") or "UNKNOWN").upper()
    if "US500" in text or "SP500" in text:
        return "SP500"
    if "NAS100" in text:
        return "NAS100"
    if "DJ30" in text:
        return "DJ30"
    if "BTC" in text:
        return "BTCUSD"
    if "GER40" in text:
        return "GER40"
    return text.replace(".R", "")[:8]


def menu_message(title: str, body: list[str], footer: bool = True) -> str:
    lines = [title, ""]
    lines.extend(line for line in body if line is not None)
    if footer:
        lines.extend(["", menu_timestamp()])
    return "\n".join(lines).rstrip()


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    if InlineKeyboardButton and InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
        )
        return markup.to_dict()
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def site_inline_keyboard() -> dict:
    if InlineKeyboardButton and InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(SITE_BUTTON_TEXT, url=DASHBOARD_URL)]])
        return markup.to_dict()
    return {"inline_keyboard": [[{"text": SITE_BUTTON_TEXT, "url": DASHBOARD_URL}]]}


def site_message() -> str:
    return "Открыть SMOB:"


def main_menu_keyboard() -> dict:
    return dashboard_keyboard()


def menu_back_keyboard(refresh: str, back: Optional[str] = None) -> dict:
    row = [("🔁 Обновить", refresh)]
    if back:
        row.append((" Назад", back))
    return inline_keyboard([row])


def menu_main_text() -> str:
    return "\n".join([" TRADING CONTROL", "", "Система управления торговыми ботами", menu_timestamp()])


def dashboard_url_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    inline_rows = []
    for row in rows:
        inline_row = []
        for text, target in row:
            if target == "dashboard_url":
                inline_row.append({"text": text, "url": DASHBOARD_URL})
            else:
                inline_row.append({"text": text, "callback_data": target})
        inline_rows.append(inline_row)
    return {"inline_keyboard": inline_rows}


def smob_inline_menu() -> dict:
    return dashboard_url_keyboard(
        [
            [(ACCOUNT_POSITIONS_BUTTON_TEXT, "refresh_account_positions")],
            [(BIAS_BUTTON_TEXT, "refresh_bias"), (SIGNALS_BUTTON_TEXT, "refresh_signals")],
            [(TRADES_BUTTON_TEXT, "refresh_processed_trades"), (ANALYTICS_BUTTON_TEXT, "refresh_analytics")],
            [(SITE_BUTTON_TEXT, "dashboard_url")],
        ]
    )


def render_command_center() -> tuple[str, dict]:
    pnl = safe_call(acct.native_pnl_today, {})
    storage = safe_call(acct.storage_health, {})
    live_bias = bias_store.latest_live_bias()
    signals = signal_store.latest_signals(limit=200)
    valid = sum(1 for item in signals if item.get("verdict") == "VALID_SIGNAL")
    watch = sum(1 for item in signals if item.get("verdict") in {"WATCH_ONLY", "WAIT_CONFIRMATION"})
    rejected = sum(1 for item in signals if item.get("verdict") in {"REJECTED", "DUPLICATE"})
    storage_ok = storage.get("db_storage") == "render_persistent_disk" and storage.get("db_file_exists")
    text = "\n".join([
        "🎛 ПАНЕЛЬ SMOB",
        "",
        "Система: 🟢 онлайн",
        f"MT5 поток: {'🟢 активен' if acct.native_data_available() else '🟡 ожидание'}",
        f"Хранилище: {'🟢 persistent' if storage_ok else '🟡 внимание'}",
        f"Байес Live: {'🟢 активен' if live_bias else '🟡 нет снимка'}",
        "Сигналы: 🟢 активны",
        "",
        "Сегодня:",
        f"PnL: {fmt_signal_money(first_present(pnl.get('closed_pnl'), pnl.get('net_pnl'), 0))}",
        f"Открыто позиций: {len(acct.current_native_positions())}",
        f"Закрыто сегодня: {first_present(pnl.get('trades_count'), pnl.get('closed_trades_count'), 0)}",
        "Риск: NORMAL",
        "",
        "Сигналы:",
        f"Валидные: {valid}",
        f"Наблюдение: {watch}",
        f"Отклонено: {rejected}",
        "",
        f"Обновлено: {short_now()}",
    ])
    return text, smob_inline_menu()


def render_account_positions_screen() -> tuple[str, dict]:
    account = acct.get_latest_native_account() or {}
    positions = acct.current_native_positions()
    lines = [
        "📊 СЧЁТ И ПОЗИЦИИ",
        "",
        "Счёт:",
        f"Баланс: {fmt_signal_money(account.get('balance'))}",
        f"Equity: {fmt_signal_money(account.get('equity'))}",
        f"Свободная маржа: {fmt_signal_money(account.get('free_margin'))}",
        f"Margin level: {dash_text(account.get('margin_level'))}%",
        "",
        "Открытые позиции:",
    ]
    if positions:
        for position in positions[:6]:
            profit = fmt_signal_money(position.get("profit"))
            tp_state = " ".join(
                [
                    f"TP1 {'✅' if position.get('tp1_done') else '⬜'}",
                    f"TP2 {'✅' if position.get('tp2_done') else '⬜'}",
                    f"TP3 {'✅' if position.get('tp3_done') else '⬜'}",
                ]
            )
            lines.extend(
                [
                    "",
                    f"{dash_text(position.get('symbol'))} | {dash_text(position.get('side'))}",
                    f"Вход: {dash_text(position.get('entry_price') or position.get('entry'))}",
                    f"SL: {dash_text(position.get('sl'))}",
                    f"PnL: {profit}",
                    tp_state,
                ]
            )
    else:
        lines.append("Нет открытых позиций.")
    lines.extend(["", f"Обновлено: {short_now()}"])
    return "\n".join(lines), smob_inline_menu()


def render_live_bias_screen() -> tuple[str, dict]:
    rows = bias_store.latest_live_bias()
    if rows:
        quality = round(sum(float(row.get("data_quality_score") or 0) for row in rows) / len(rows))
        risk = "HIGH" if any(row.get("risk") == "HIGH" for row in rows) else "MEDIUM" if any(row.get("risk") == "MEDIUM" for row in rows) else "LOW"
        lines = ["📈 БАЙЕС LIVE", ""]
        for row in rows:
            suffix = " ⚠️ слабый" if str(row.get("strength") or "").upper() == "WEAK" else ""
            lines.append(f"{dash_text(row.get('symbol')):<7} {dash_text(row.get('direction')):<5} {dash_text(row.get('confidence'))}% {dash_text(row.get('strength'))}{suffix}")
        lines.extend(["", f"Риск: {risk}", f"Качество данных: {quality}%", f"Обновлено: {short_now()}"])
        text = "\n".join(lines)
    else:
        text = "📈 БАЙЕС LIVE\n\nСнимка байеса пока нет."
    return text, smob_inline_menu()


def render_signal_board() -> tuple[str, dict]:
    signals = signal_store.latest_signals(limit=50)
    valid = [s for s in signals if s.get("verdict") == "VALID_SIGNAL"][:4]
    watch = [s for s in signals if s.get("verdict") in {"WATCH_ONLY", "WAIT_CONFIRMATION"}][:4]
    rejected = [s for s in signals if s.get("verdict") in {"REJECTED", "DUPLICATE", "EXPIRED"}][:4]
    lines = ["⚡ СИГНАЛЫ", "", "Валидные:"]
    lines.extend(signal_line(item) for item in valid)
    if not valid:
        lines.append("Валидных сигналов пока нет.")
    lines.extend(["", "Наблюдение:"])
    lines.extend(signal_line(item) for item in watch)
    if not watch:
        lines.append("Сигналов в наблюдении пока нет.")
    lines.extend(["", "Отклонённые:"])
    lines.extend(rejected_line(item) for item in rejected)
    if not rejected:
        lines.append("Отклонённых сигналов пока нет.")
    lines.extend(["", f"Обновлено: {short_now()}"])
    return "\n".join(lines), smob_inline_menu()


def render_processed_trades_signals() -> tuple[str, dict]:
    mt5 = acct.native_trade_events(limit=5)
    signals = signal_store.latest_signals(limit=5)
    evals = {item.get("signal_id"): item for item in signal_store.signal_evaluations(limit=100)}
    lines = ["🧾 СДЕЛКИ И СИГНАЛЫ", "", "MT5:"]
    if mt5:
        for event in mt5[:3]:
            lines.extend([f"{dash_text(event.get('symbol'))} {dash_text(event.get('side') or event.get('event_type'))}", f"PnL: {fmt_signal_money(event.get('profit'))}", ""])
    else:
        lines.append("MT5 событий пока нет.")
    lines.append("Сигналы:")
    if signals:
        for signal in signals[:4]:
            ev = evals.get(signal.get("signal_id")) or {}
            lines.extend([f"{dash_text(signal.get('symbol'))} {dash_text(signal.get('direction'))}", f"Оценка: {dash_text(signal.get('score'))}%", f"Статус: {dash_text(signal.get('verdict'))}", f"Результат: {dash_text(ev.get('result') or 'ожидание')}", ""])
    else:
        lines.append("Обработанных сигналов пока нет.")
    return "\n".join(lines).strip(), smob_inline_menu()


def render_system_statistics_screen() -> tuple[str, dict]:
    accuracy = evaluate_signal_accuracy(limit=1000)
    sources = signal_store.source_reliability()
    best_source = sources[0] if sources else {}
    bias_accuracy = safe_bias_accuracy_summary()
    signals = signal_store.latest_signals(limit=500)
    overall = accuracy.get("overall", {}).get("all", {})
    lines = ["📉 АНАЛИТИКА", "", "Торговля:", "Сделки: —", "Winrate: —", "PF: —", "Avg R: —", "", "Сигналы:", f"Обработано: {accuracy.get('signal_count', 0)}", f"Валидные: {sum(1 for s in signals if s.get('verdict') == 'VALID_SIGNAL')}", f"Верно: {overall.get('correct', 0)}", f"Ошибки: {overall.get('wrong', 0)}", f"Точность: {dash_text(overall.get('accuracy'))}%", "", "Байес:", f"30m точность: {bias_accuracy.get('30m')}", f"1h точность: {bias_accuracy.get('1h')}", f"Лучший символ: {bias_accuracy.get('best_symbol')}", f"Худший символ: {bias_accuracy.get('worst_symbol')}", "", "Источники:", f"Лучший источник: {dash_text(best_source.get('source_name'))}", f"Trust: {dash_text(best_source.get('trust_score'))}/100"]
    return "\n".join(lines), smob_inline_menu()


def render_signal_risk_screen() -> tuple[str, dict]:
    pnl = safe_call(acct.native_pnl_today, {})
    storage = safe_call(acct.storage_health, {})
    risky = [s for s in signal_store.latest_signals(limit=100) if s.get("risk_level") == "HIGH"]
    lines = ["🛡 КОНТРОЛЬ РИСКА", "", "Статус: NORMAL", "", f"PnL сегодня: {fmt_signal_money(first_present(pnl.get('closed_pnl'), pnl.get('net_pnl'), 0))}", "Открытый риск: —", "Worst SL Damage: —", f"High Risk Signals: {len(risky)}", "", "Предупреждения:"]
    lines.extend([f"⚠️ {item.get('symbol')} signal {item.get('risk_level')}" for item in risky[:4]] or ["—"])
    lines.extend(["", f"Хранилище: {'SAFE' if storage.get('db_storage') == 'render_persistent_disk' else 'WARNING'}", "История: OK"])
    return "\n".join(lines), smob_inline_menu()


def render_signal_sources_screen() -> tuple[str, dict]:
    sources = signal_store.source_reliability()
    lines = ["🧠 ИСТОЧНИКИ СИГНАЛОВ", ""]
    if not sources:
        lines.append("Источники сигналов пока не подключены.")
    for source in sources[:8]:
        lines.extend([dash_text(source.get("source_name")), f"Trust: {dash_text(source.get('trust_score'))}/100", f"Signals: {source.get('total_signals', 0)}", f"Accuracy: {dash_text(source.get('winrate'))}%", f"Avg R: {dash_text(source.get('average_R'))}", ""])
    return "\n".join(lines).strip(), smob_inline_menu()


def render_bias_accuracy_screen() -> tuple[str, dict]:
    from .live_bias_accuracy import live_bias_accuracy

    data = live_bias_accuracy(limit=1000)
    overall = data.get("overall", {})
    lines = ["📊 ТОЧНОСТЬ БАЙЕСА", "", f"30m: {dash_text((overall.get('30m') or {}).get('accuracy'))}%", f"1h: {dash_text((overall.get('1h') or {}).get('accuracy'))}%", f"2h: {dash_text((overall.get('2h') or {}).get('accuracy'))}%", f"4h: {dash_text((overall.get('4h') or {}).get('accuracy'))}%", f"Оценено: {data.get('evaluated_count', 0)}"]
    return "\n".join(lines), smob_inline_menu()


def render_signal_accuracy_screen() -> tuple[str, dict]:
    data = evaluate_signal_accuracy(limit=1000)
    overall = data.get("overall", {}).get("all", {})
    lines = ["📊 ТОЧНОСТЬ СИГНАЛОВ", "", f"Сигналы: {data.get('signal_count', 0)}", f"Верно: {overall.get('correct', 0)}", f"Ошибки: {overall.get('wrong', 0)}", f"Нейтрально: {overall.get('neutral', 0)}", f"Точность: {dash_text(overall.get('accuracy'))}%"]
    return "\n".join(lines), smob_inline_menu()


def menu_send(chat_id: str, text: str, reply_markup: Optional[dict] = None, edit_message_id: Optional[int] = None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    method = "editMessageText" if edit_message_id else "sendMessage"
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    payload = {
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if edit_message_id:
        payload["message_id"] = edit_message_id
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=8):
            return True
    except Exception:
        if edit_message_id:
            payload.pop("message_id", None)
            data = urllib.parse.urlencode(payload).encode("utf-8")
            try:
                request = urllib.request.Request(
                    f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                    data=data,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=8):
                    return True
            except Exception:
                return False
        return False


def answer_callback_query(callback_query_id: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    data = urllib.parse.urlencode({"callback_query_id": callback_query_id}).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            return
    except Exception:
        return


def handle_telegram_update(update: dict) -> bool:
    try:
        callback = update.get("callback_query") or {}
        if callback:
            answer_callback_query(str(callback.get("id") or ""))
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id")) if chat.get("id") is not None else None
            if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
                return True
            text, keyboard = render_menu_callback(str(callback.get("data") or ""), chat_id or "")
            menu_send(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, text, keyboard, message.get("message_id"))
            return True

        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else None
        text = str(message.get("text") or "").strip()
        if not chat_id or not text:
            return False
        if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
            return True
        if text.split()[0].lower() in ("/start", "/menu"):
            user_state[chat_id] = {"screen": "center"}
            text_out, keyboard = render_command_center()
            menu_send(chat_id, text_out, keyboard)
            return True
        if text in (SITE_BUTTON_TEXT, "Сайт") or text.split()[0].lower() in ("/site", "/dashboard"):
            menu_send(chat_id, site_message(), site_inline_keyboard())
            return True
        if text.split()[0].lower() == "/backtest":
            if len(text.split()) > 1:
                asset = text.split()[1].upper()
                text_out, keyboard = render_backtest_result(asset)
            else:
                text_out, keyboard = render_backtest_selector()
            menu_send(chat_id, text_out, keyboard)
            return True
        if text in MENU_BUTTON_CALLBACKS:
            callback_data = MENU_BUTTON_CALLBACKS[text]
            text_out, keyboard = render_menu_callback(callback_data, chat_id)
            menu_send(chat_id, text_out, keyboard)
            return True
        return False
    except Exception:
        try:
            chat_id = config.TELEGRAM_ADMIN_CHAT_ID
            menu_send(chat_id, menu_message("🔴 ОШИБКА", ["Не удалось обработать запрос."], True), main_menu_keyboard())
        except Exception:
            pass
        return True


def render_menu_callback(data: str, chat_id: str) -> tuple[str, dict]:
    try:
        if data in {"refresh_center", "menu_main"}:
            user_state[chat_id] = {"screen": "center"}
            return render_command_center()
        if data == "refresh_account_positions":
            return render_account_positions_screen()
        if data == "refresh_bias":
            return render_live_bias_screen()
        if data == "refresh_signals":
            return render_signal_board()
        if data in {"refresh_processed_trades", "menu_processed_trades"}:
            return render_processed_trades_signals()
        if data == "refresh_analytics":
            return render_system_statistics_screen()
        if data == "refresh_stats":
            return render_system_statistics_screen()
        if data == "refresh_risk":
            return render_signal_risk_screen()
        if data == "refresh_sources":
            return render_signal_sources_screen()
        if data == "bias_accuracy":
            return render_bias_accuracy_screen()
        if data == "bias_calibration":
            text = "🧠 КАЛИБРОВКА БАЙЕСА\n\nПолные рекомендации доступны в SMOB.\nТребуется ручное подтверждение: true"
            return text, smob_inline_menu()
        if data == "signal_accuracy":
            return render_signal_accuracy_screen()
        if data in {"signals_valid", "signals_watch", "signals_rejected", "signals_risky"}:
            return render_signal_board()
        if data.startswith("signal_details:"):
            return render_signal_detail_screen(data.split(":", 1)[1])
        if data.startswith("source_details:"):
            return render_source_detail_screen(data.split(":", 1)[1])
        if data == "menu_main":
            user_state[chat_id] = {"screen": "main"}
            return menu_main_text(), main_menu_keyboard()
        if data == "menu_status":
            return render_menu_status()
        if data == "menu_trades":
            user_state.setdefault(chat_id, {})["screen"] = "trades_source"
            return render_trades_source()
        if data == "trades_back_src":
            user_state.setdefault(chat_id, {})["screen"] = "trades_source"
            return render_trades_source()
        if data.startswith("trades_src_"):
            source = data.replace("trades_src_", "", 1)
            state = user_state.setdefault(chat_id, {})
            state["screen"] = "trades_period"
            state["trades_source"] = source
            return render_trades_period()
        if data.startswith("trades_p_"):
            period = data.replace("trades_p_", "", 1)
            state = user_state.setdefault(chat_id, {})
            state["screen"] = "trades_result"
            state["trades_period"] = period
            source = state.get("trades_source", "bot")
            return render_trades_result(source, period)
        if data == "trades_refresh":
            state = user_state.setdefault(chat_id, {})
            return render_trades_result(state.get("trades_source", "bot"), state.get("trades_period", "today"))
        if data == "backtest_menu":
            user_state.setdefault(chat_id, {})["screen"] = "backtest_select"
            return render_backtest_selector()
        if data.startswith("backtest_"):
            asset = data.replace("backtest_", "", 1)
            user_state.setdefault(chat_id, {})["screen"] = "backtest_result"
            return render_backtest_result(asset)
        if data == "menu_stats":
            user_state.setdefault(chat_id, {})["screen"] = "stats_source"
            return render_stats_source()
        if data.startswith("stats_src_"):
            source = data.replace("stats_src_", "", 1)
            state = user_state.setdefault(chat_id, {})
            state["screen"] = "stats_period"
            state["stats_source"] = source
            return render_stats_period()
        if data.startswith("stats_p_"):
            period = data.replace("stats_p_", "", 1)
            state = user_state.setdefault(chat_id, {})
            state["screen"] = "stats_asset"
            state["stats_period"] = period
            return render_stats_asset()
        if data.startswith("stats_a_"):
            asset = data.replace("stats_a_", "", 1)
            period = user_state.get(chat_id, {}).get("stats_period", "day")
            state = user_state.setdefault(chat_id, {})
            state["screen"] = "stats_result"
            state["stats_asset"] = asset
            return render_stats_result(state.get("stats_source", "bot"), period, asset)
        if data == "stats_refresh":
            state = user_state.get(chat_id, {})
            return render_stats_result(state.get("stats_source", "bot"), state.get("stats_period", "day"), state.get("stats_asset", "ALL"))
        if data == "stats_back_asset":
            return render_stats_asset()
        if data == "menu_actions":
            user_state[chat_id] = {"screen": "actions"}
            return render_actions_menu()
        if data in {"action_enable", "action_disable"}:
            action = "enable" if data == "action_enable" else "disable"
            user_state[chat_id] = {"screen": "action_select", "action": action}
            return render_action_select(action)
        if data in {"action_enable_all", "action_disable_all"}:
            action = "enable_all" if data == "action_enable_all" else "disable_all"
            user_state[chat_id] = {"screen": "action_confirm", "action": action, "asset": "ALL"}
            return render_action_confirm(action, "ALL")
        if data.startswith("act_bot_"):
            asset = data.replace("act_bot_", "", 1)
            action = user_state.get(chat_id, {}).get("action", "enable")
            user_state[chat_id] = {"screen": "action_confirm", "action": action, "asset": asset}
            return render_action_confirm(action, asset)
        if data == "act_confirm":
            state = user_state.get(chat_id, {})
            return execute_action(state.get("action", "enable"), state.get("asset", "NAS100"))
        if data == "action_report":
            text, keyboard = render_daily_report_menu()
            send_telegram_message(text)
            return text, keyboard
        if data == "menu_market":
            return render_market()
        if data == "menu_records":
            return render_records()
        if data == "menu_risk":
            return render_risk()
        if data == "menu_bots":
            return render_bots_status()
        return menu_main_text(), main_menu_keyboard()
    except Exception:
        return menu_message("🔴 ОШИБКА", ["Данные временно недоступны."], True), main_menu_keyboard()


def render_menu_status() -> tuple[str, dict]:
    try:
        account = acct.latest_account_snapshot() or {}
        pnl = acct.pnl_today() or {}
        controls = acct.list_native_bot_controls(include_defaults=True)
        trades = int(pnl.get("trades_count") or pnl.get("closed_trades_count") or 0)
        wins = int(pnl.get("wins") or 0)
        losses = int(pnl.get("losses") or 0)
        winrate = round((wins / trades) * 100) if trades else 0
        body = [
            f"💰 Баланс:    {menu_money(account.get('balance'), '$') or '$0.00'}",
            f"📊 Equity:    {menu_money(account.get('equity'), '$') or '$0.00'}",
            f"📉 Margin:    {menu_money(account.get('margin'), '$') or '$0.00'}",
            f" Свободно:  {menu_money(account.get('free_margin'), '$') or '$0.00'}",
            "",
            "📅 СЕГОДНЯ",
            f"P&L:      {menu_money(first_present(pnl.get('closed_pnl'), pnl.get('net_pnl'), pnl.get('total_pnl')), '$', signed=True) or '+$0.00'}",
            f"Сделок:    {trades}  ({wins}W / {losses}L)",
            f"Винрейт:   {winrate}%",
            "",
            "🤖 БОТЫ",
        ]
        by_asset = {control.get("asset"): control for control in controls}
        bot_parts = []
        for asset in MENU_ASSETS:
            control = by_asset.get(asset) or {}
            bot_parts.append(f"{asset:<6} {'🟢' if control_enabled(control) else '🔴'}")
        for index in range(0, len(bot_parts), 2):
            body.append("  ".join(bot_parts[index:index + 2]))
        return menu_message("📊 СТАТУС АККАУНТА", body), menu_back_keyboard("menu_status")
    except Exception:
        return menu_message("🔴 ОШИБКА СТАТУСА", ["Не удалось получить данные аккаунта."], True), menu_back_keyboard("menu_status")


def render_trades_source() -> tuple[str, dict]:
    text = menu_message("📋 СДЕЛКИ  ИСТОЧНИК", [], False)
    keyboard = inline_keyboard(
        [
            [("🤖 Только бот", "trades_src_bot"), ("👤 Все сделки", "trades_src_all")],
            [("📊 Бэктест", "trades_src_backtest")],
        ]
    )
    return text, keyboard


def render_trades_period() -> tuple[str, dict]:
    text = menu_message("📋 СДЕЛКИ  ПЕРИОД", [], False)
    keyboard = inline_keyboard(
        [
            [("Сегодня", "trades_p_today"), ("Вчера", "trades_p_yesterday"), ("Неделя", "trades_p_week")],
        ]
    )
    return text, keyboard


def period_to_store(period: str) -> str:
    return {"today": "today", "yesterday": "yesterday", "week": "7d", "day": "today", "month": "30d"}.get(period, "today")


def period_title(period: str) -> str:
    return {"today": "СЕГОДНЯ", "yesterday": "ВЧЕРА", "week": "НЕДЕЛЯ", "day": "ДЕНЬ", "month": "МЕСЯЦ", "all": "ВСЁ ВРЕМЯ"}.get(period, period.upper())


def menu_journal(period: str, selector: Optional[str] = None, limit: int = 500) -> list[dict]:
    if period == "yesterday":
        start = datetime.now(BERLIN_TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end = start + timedelta(days=1)
        rows = acct.native_journal_all(limit=10000)
        result = []
        for row in rows:
            parsed = parse_datetime(first_present(row.get("closed_at"), row.get("opened_at"), row.get("created_at")))
            if not parsed:
                continue
            local = parsed.astimezone(BERLIN_TZ)
            if start <= local < end and (not selector or selector == "all" or selector in menu_asset_from_trade(row)):
                result.append(row)
        return result[:limit]
    selector_arg = None if selector in (None, "all", "Все") else selector
    return acct.journal_entries(period_to_store(period), selector_arg, limit=limit)


def source_icon(source: str) -> str:
    return {"bot": "🤖", "manual": "👤", "all": "👤", "backtest": "📊"}.get(str(source or "bot").lower(), "🤖")


def source_label(source: str) -> str:
    return {"bot": "🤖 БОТ", "manual": "👤 РУЧНЫЕ", "all": "👤 ВСЕ СДЕЛКИ", "backtest": "📊 БЭКТЕСТ"}.get(str(source or "bot").lower(), "🤖 БОТ")


def status_icon(row: dict) -> str:
    status = str(row.get("status") or "").lower()
    profit = float_or_zero(first_present(row.get("profit_money"), row.get("profit")))
    if status == "open" or not first_present(row.get("close_time"), row.get("closed_at")):
        return "🔵"
    if profit > 0 or status == "win":
        return "🟢"
    if profit < 0 or status == "loss":
        return "🔴"
    return "🟡"


def render_trades_result(source: str, period: str) -> tuple[str, dict]:
    try:
        rows = acct.get_trades_filtered(source=source, period=period, asset="ALL", limit=500)
        total = sum(float_or_zero(first_present(r.get("profit_money"), r.get("profit"))) for r in rows)
        wins = sum(1 for r in rows if float_or_zero(first_present(r.get("profit_money"), r.get("profit"))) > 0)
        losses = sum(1 for r in rows if float_or_zero(first_present(r.get("profit_money"), r.get("profit"))) < 0)
        closed = wins + losses
        winrate = round((wins / closed) * 100) if closed else 0
        body = []
        for row in rows[:10]:
            profit = float_or_zero(first_present(row.get("profit_money"), row.get("profit")))
            pnl = menu_number(profit, 2, signed=True)
            risk_value = safe_float(row.get("r_multiple"))
            risk = f"{risk_value:.2f}R" if risk_value is not None else estimate_r_multiple(row)
            duration = format_minutes(row.get("duration_minutes")) if row.get("duration_minutes") is not None else menu_duration(row.get("opened_at"), row.get("closed_at"))
            parts = [status_icon(row), source_icon(row.get("source") or source), menu_asset_from_trade(row).ljust(7), fmt_side(row.get("side")).ljust(4)]
            if pnl:
                parts.append(pnl.rjust(5))
            if risk:
                parts.append(risk.rjust(6))
            if duration:
                parts.append(duration)
            body.append(" ".join(parts))
        if not body:
            body.append("Сделок пока нет")
        body.extend(["", f"Итого: {menu_number(total, 2, True) or '+0.00'} | {wins}W / {losses}L | {winrate}%"])
        return menu_message(f"📋 {source_label(source)}  {period_title(period)}", body), inline_keyboard([[("🔁 Обновить", "trades_refresh"), ("Источник", "trades_back_src")]])
    except Exception:
        return menu_message("🔴 ОШИБКА СДЕЛОК", ["Не удалось получить журнал сделок."], True), inline_keyboard([[("🔁 Обновить", "trades_refresh"), ("Источник", "trades_back_src")]])


def render_backtest_selector() -> tuple[str, dict]:
    text = menu_message("📊 БЭКТЕСТ  БОТ", [], False)
    keyboard = inline_keyboard(
        [
            [("Все", "backtest_ALL")],
            [("NAS100", "backtest_NAS100"), ("SP500", "backtest_SP500")],
            [("DJ30", "backtest_DJ30"), ("BTCUSD", "backtest_BTCUSD")],
            [("GER40", "backtest_GER40")],
        ]
    )
    return text, keyboard


def format_backtest_result(asset: str = "ALL") -> str:
    selector = None if str(asset or "ALL").upper() == "ALL" else asset
    summary = acct.backtest_summary(selector)
    rows = acct.backtest_trades(bot_id=selector, limit=10)
    title_asset = str(asset or "ALL").upper()
    body = [
        f"Сделок:      {summary.get('total_trades', 0)}",
        f"Винрейт:     {summary.get('win_rate', 0)}%",
        f"P&L:         {menu_number(summary.get('total_pnl'), 2, True) or '+0.00'}",
    ]
    best = summary.get("best")
    worst = summary.get("worst")
    if best:
        body.append(f"Лучшая:      {menu_asset_from_trade(best)} {menu_number(best.get('profit_money'), 2, True) or '+0.00'}")
    if worst:
        body.append(f"Худшая:      {menu_asset_from_trade(worst)} {menu_number(worst.get('profit_money'), 2, True) or '+0.00'}")
    body.extend(["", "Последние сделки:"])
    if not rows:
        body.append("Сделок пока нет")
    for row in rows:
        profit = float_or_zero(row.get("profit_money"))
        icon = "🟢" if profit > 0 else "🔴" if profit < 0 else "🟡"
        duration = menu_duration(row.get("open_time"), row.get("close_time"))
        parts = [icon, menu_asset_from_trade(row).ljust(7), fmt_side(row.get("side")).ljust(4), (menu_number(profit, 2, True) or "+0.00").rjust(5)]
        if duration:
            parts.append(duration)
        body.append(" ".join(parts))
    return menu_message(f"📊 БЭКТЕСТ  {title_asset}", body)


def render_backtest_result(asset: str = "ALL") -> tuple[str, dict]:
    return format_backtest_result(asset), menu_back_keyboard(f"backtest_{asset}", "backtest_menu")


def estimate_r_multiple(row: dict) -> Optional[str]:
    profit = safe_float(row.get("profit"))
    entry = safe_float(row.get("entry"))
    sl = safe_float(row.get("sl"))
    if profit is None or entry is None or sl is None or entry == sl:
        return None
    lot = abs(safe_float(row.get("lot")) or 1.0)
    risk = abs(entry - sl) * lot
    if risk <= 0:
        return None
    return f"{profit / risk:.2f}R"


def render_stats_source() -> tuple[str, dict]:
    text = menu_message("📈 СТАТИСТИКА  ИСТОЧНИК", [], False)
    keyboard = inline_keyboard(
        [
            [("🤖 Только бот", "stats_src_bot"), ("👤 Все сделки", "stats_src_all")],
            [("📊 Бэктест", "stats_src_backtest")],
        ]
    )
    return text, keyboard


def render_stats_period() -> tuple[str, dict]:
    text = menu_message("📈 СТАТИСТИКА  ПЕРИОД", [], False)
    keyboard = inline_keyboard(
        [
            [("День", "stats_p_day"), ("Неделя", "stats_p_week")],
            [("Месяц", "stats_p_month"), ("Всё время", "stats_p_all")],
        ]
    )
    return text, keyboard


def render_stats_asset() -> tuple[str, dict]:
    text = menu_message("📈 СТАТИСТИКА  АКТИВ", [], False)
    keyboard = inline_keyboard(
        [
            [("Все", "stats_a_ALL"), ("NAS100", "stats_a_NAS100"), ("SP500", "stats_a_SP500")],
            [("DJ30", "stats_a_DJ30"), ("BTCUSD", "stats_a_BTCUSD"), ("GER40", "stats_a_GER40")],
        ]
    )
    return text, keyboard


def render_stats_result(source: str, period: str, asset: str) -> tuple[str, dict]:
    try:
        stats = acct.get_stats_filtered(source=source, period=period, asset=asset)
        trades = int(stats.get("total_trades") or 0)
        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        win_pct = round((wins / trades) * 100) if trades else 0
        loss_pct = round((losses / trades) * 100) if trades else 0
        title_asset = "ВСЕ" if str(asset).upper() == "ALL" else asset
        body = [
            f"🎯 Сделок:         {trades}",
            f" Побед:           {wins} ({win_pct}%)",
            f" Убытков:         {losses} ({loss_pct}%)",
            "",
            f"💰 P&L:        {menu_number(stats.get('total_pnl'), 2, True) or '+0.00'}",
            f"🏆 Лучшая:     {menu_number(stats.get('best_trade'), 2, True) or '+0.00'}",
            f"📉 Худшая:      {menu_number(stats.get('worst_trade'), 2, True) or '0.00'}",
            f" Средняя:    {menu_number(stats.get('avg_trade'), 2, True) or '+0.00'}",
            "",
            "📊 МЕТРИКИ",
        ]
        body.append(f"Profit Factor:   {float_or_zero(stats.get('profit_factor')):.2f}")
        if stats.get("avg_r") is not None:
            body.append(f"Avg R:           {float_or_zero(stats.get('avg_r')):.2f}R")
        if trades:
            body.append(f"TP1 взят:        {round(float_or_zero(stats.get('tp1_hit_rate')))}%")
            body.append(f"TP2 взят:        {round(float_or_zero(stats.get('tp2_hit_rate')))}%")
        return menu_message(f"📈 {source_label(source)} | {title_asset} | {period_title(period)}", body), inline_keyboard([[("🔁 Обновить", "stats_refresh"), ("Актив", "stats_back_asset")]])
    except Exception:
        return menu_message("🔴 ОШИБКА СТАТИСТИКИ", ["Не удалось рассчитать статистику."], True), inline_keyboard([[("🔁 Обновить", "stats_refresh"), ("Актив", "stats_back_asset")]])


def average_r(rows: list[dict]) -> Optional[float]:
    values = []
    for row in rows:
        r_text = estimate_r_multiple(row)
        if r_text:
            values.append(float_or_zero(r_text.replace("R", "")))
    return round(sum(values) / len(values), 2) if values else None


def average_duration(rows: list[dict]) -> Optional[str]:
    minutes = []
    for row in rows:
        opened = parse_datetime(row.get("opened_at"))
        closed = parse_datetime(row.get("closed_at"))
        if opened and closed:
            minutes.append(max(0, int((closed - opened).total_seconds() // 60)))
    if not minutes:
        return None
    avg = round(sum(minutes) / len(minutes))
    if avg >= 60:
        hours, mins = divmod(avg, 60)
        return f"{hours}ч {mins}м"
    return f"{avg}м"


def render_actions_menu() -> tuple[str, dict]:
    text = menu_message(" УПРАВЛЕНИЕ", [], False)
    keyboard = inline_keyboard(
        [
            [(" Включить бота", "action_enable")],
            [(" Остановить бота", "action_disable")],
            [(" Включить всех", "action_enable_all")],
            [(" Стоп все боты", "action_disable_all")],
            [("📋 Отчёт сейчас", "action_report")],
        ]
    )
    return text, keyboard


def render_action_select(action: str) -> tuple[str, dict]:
    title = "🤖 ВЫБЕРИ БОТА"
    keyboard = inline_keyboard(
        [
            [("NAS100", "act_bot_NAS100"), ("SP500", "act_bot_SP500")],
            [("DJ30", "act_bot_DJ30"), ("BTCUSD", "act_bot_BTCUSD")],
            [("GER40", "act_bot_GER40")],
            [(" Назад", "menu_actions")],
        ]
    )
    return menu_message(title, [], False), keyboard


def render_action_confirm(action: str, asset: str) -> tuple[str, dict]:
    verb = "Включить" if action in {"enable", "enable_all"} else "Остановить"
    target = "всех ботов" if asset == "ALL" or action.endswith("_all") else f"бота: {asset}"
    text = menu_message(" ПОДТВЕРЖДЕНИЕ", [f"{verb} {target}?"], False)
    keyboard = inline_keyboard([[(" Подтвердить", "act_confirm"), (" Отмена", "menu_actions")]])
    return text, keyboard


def execute_action(action: str, asset: str) -> tuple[str, dict]:
    try:
        enable = action in {"enable", "enable_all"}
        if asset == "ALL" or action.endswith("_all"):
            controls = acct.set_all_native_bots_enabled(enable, "Telegram menu")
            target = "ВСЕ БОТЫ"
        else:
            control = acct.set_native_bot_enabled(asset, enable, "Telegram menu")
            controls = [control] if control else []
            target = asset
        status = "ВКЛЮЧЁН" if enable else "ВЫКЛЮЧЕН"
        body = [f"🤖 {target}  {status}"]
        if not controls:
            body = ["🔴 Бот не найден"]
        return menu_message(" ВЫПОЛНЕНО", body, False), inline_keyboard([])
    except Exception:
        return menu_message("🔴 ОШИБКА", ["Действие не выполнено."], False), inline_keyboard([])


def render_daily_report_menu() -> tuple[str, dict]:
    try:
        rows = [r for r in menu_journal("today", limit=500) if r.get("closed_at")]
        total = sum(float_or_zero(r.get("profit")) for r in rows)
        wins = sum(1 for r in rows if float_or_zero(r.get("profit")) > 0)
        losses = sum(1 for r in rows if float_or_zero(r.get("profit")) < 0)
        trades = wins + losses
        winrate = round((wins / trades) * 100) if trades else 0
        by_asset = {}
        for row in rows:
            by_asset[menu_asset_from_trade(row)] = by_asset.get(menu_asset_from_trade(row), 0.0) + float_or_zero(row.get("profit"))
        best = max(rows, key=lambda r: float_or_zero(r.get("profit")), default=None)
        worst = min(rows, key=lambda r: float_or_zero(r.get("profit")), default=None)
        body = [
            f"📅 {datetime.now(BERLIN_TZ).strftime('%d.%m.%Y')}",
            "",
            f"💰 P&L дня:    {menu_number(total, 2, True) or '+0.00'}",
            f"📊 Сделок:      {trades}",
            f" Побед:        {wins} ({winrate}%)",
            f" Убытков:      {losses}",
            "",
            "По активам:",
        ]
        for asset in MENU_ASSETS:
            if asset in by_asset:
                body.append(f"{asset:<8} {menu_number(by_asset[asset], 2, True)}")
        body.extend([
            "",
            f"🏆 Лучшая:   {menu_asset_from_trade(best) if best else ''} {menu_number(best.get('profit'), 2, True) if best else ''}".rstrip(),
            f"📉 Худшая:   {menu_asset_from_trade(worst) if worst else ''} {menu_number(worst.get('profit'), 2, True) if worst else ''}".rstrip(),
        ])
        return menu_message("📅 ДНЕВНОЙ ОТЧЁТ", body, False), inline_keyboard([])
    except Exception:
        return menu_message("🔴 ОШИБКА ОТЧЁТА", ["Не удалось сформировать отчёт."], False), inline_keyboard([])


def render_market() -> tuple[str, dict]:
    body = []
    try:
        import yfinance as yf
        for asset, ticker in MARKET_SYMBOLS.items():
            try:
                hist = yf.Ticker(ticker).history(period="5d", interval="1d", timeout=6)
                if hist is None or hist.empty:
                    body.append(f"{asset:<7} 🟡 Нет данных")
                    continue
                close = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
                change = ((close - prev) / prev) * 100 if prev else 0.0
                if asset == "VIX":
                    color = "🟢" if close < 20 else "🟡" if close <= 30 else "🔴"
                    level = "Низкий" if close < 20 else "Средний" if close <= 30 else "Высокий"
                    body.append("")
                    body.append(f"VIX:  {close:.2f}  {color} {level}")
                else:
                    color = "🟢" if change > 0.1 else "🔴" if change < -0.1 else "🟡"
                    body.append(f"{asset:<7} {close:>10,.2f}  {color} {change:+.2f}%")
            except Exception:
                body.append(f"{asset:<7} 🟡 Нет данных")
    except Exception:
        body.append("🟡 Нет данных")
    return menu_message("🌍 РЫНОК СЕЙЧАС", body), menu_back_keyboard("menu_market")


def render_records() -> tuple[str, dict]:
    try:
        rows = [r for r in acct.native_journal_all(limit=10000) if r.get("closed_at")]
        by_day = {}
        for row in rows:
            parsed = parse_datetime(row.get("closed_at"))
            if not parsed:
                continue
            key = parsed.astimezone(BERLIN_TZ).strftime("%d.%m")
            by_day[key] = by_day.get(key, 0.0) + float_or_zero(row.get("profit"))
        best_day = max(by_day.items(), key=lambda i: i[1], default=None)
        worst_day = min(by_day.items(), key=lambda i: i[1], default=None)
        best_trade = max(rows, key=lambda r: float_or_zero(r.get("profit")), default=None)
        worst_trade = min(rows, key=lambda r: float_or_zero(r.get("profit")), default=None)
        streak = longest_win_streak(rows)
        max_r_row, max_r = max_r_multiple(rows)
        total = sum(float_or_zero(r.get("profit")) for r in rows)
        wins = sum(1 for r in rows if float_or_zero(r.get("profit")) > 0)
        losses = sum(1 for r in rows if float_or_zero(r.get("profit")) < 0)
        trades = wins + losses
        winrate = round((wins / trades) * 100) if trades else 0
        body = [
            f"💰 Лучший день:      {menu_number(best_day[1], 2, True) if best_day else '+0.00'}  📅 {best_day[0] if best_day else '--.--'}",
            f"🎯 Лучшая сделка:    {menu_number(best_trade.get('profit'), 2, True) if best_trade else '+0.00'}  {menu_asset_from_trade(best_trade) if best_trade else ''}".rstrip(),
            f"📈 Лучшая серия:      {streak} побед подряд",
            f" Макс R:            {max_r:.2f}R  {menu_asset_from_trade(max_r_row) if max_r_row else ''}".rstrip(),
            "",
            "📉 АНТИРЕКОРДЫ",
            f"Худший день:        {menu_number(worst_day[1], 2, False) if worst_day else '0.00'}  📅 {worst_day[0] if worst_day else '--.--'}",
            f"Худшая сделка:      {menu_number(worst_trade.get('profit'), 2, False) if worst_trade else '0.00'}  {menu_asset_from_trade(worst_trade) if worst_trade else ''}".rstrip(),
            "",
            "📊 ВСЕГО",
            f"Сделок:    {trades}",
            f"P&L:      {menu_number(total, 2, True) or '+0.00'}",
            f"Винрейт:   {winrate}%",
        ]
        return menu_message("🏆 РЕКОРДЫ", body), menu_back_keyboard("menu_records")
    except Exception:
        return menu_message("🔴 ОШИБКА РЕКОРДОВ", ["Не удалось получить рекорды."], True), menu_back_keyboard("menu_records")


def longest_win_streak(rows: list[dict]) -> int:
    best = current = 0
    for row in sorted(rows, key=lambda r: str(first_present(r.get("closed_at"), r.get("created_at")))):
        if float_or_zero(row.get("profit")) > 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def max_r_multiple(rows: list[dict]) -> tuple[Optional[dict], float]:
    best_row = None
    best_value = 0.0
    for row in rows:
        r_text = estimate_r_multiple(row)
        value = float_or_zero(r_text.replace("R", "")) if r_text else 0.0
        if value > best_value:
            best_row, best_value = row, value
    return best_row, best_value


def max_drawdown_pct(rows: list[dict]) -> float:
    equity = peak = drawdown = 0.0
    for row in sorted(rows, key=lambda r: str(first_present(r.get("closed_at"), r.get("created_at")))):
        equity += float_or_zero(row.get("profit"))
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    account = acct.latest_account_snapshot() or {}
    balance = safe_float(account.get("balance")) or 1.0
    return abs(drawdown) / balance * 100


def render_risk() -> tuple[str, dict]:
    try:
        start = acct.first_native_account_snapshot_today() or {}
        account = acct.latest_account_snapshot() or {}
        pnl = acct.pnl_today() or {}
        positions = acct.current_positions()
        limit = 250.0
        current_pnl = safe_float(first_present(pnl.get("closed_pnl"), pnl.get("total_pnl"), pnl.get("net_pnl"))) or 0.0
        used = max(0.0, -current_pnl)
        used_pct = min(100, round((used / limit) * 100)) if limit else 0
        remaining = max(0.0, limit - used)
        if used >= limit:
            status = "🔴 ЛИМИТ ДОСТИГНУТ"
        elif used_pct > 70:
            status = "🔴 ЛИМИТ БЛИЗКО"
        elif used_pct >= 30:
            status = "🟡 ВНИМАНИЕ"
        else:
            status = "🟢 В НОРМЕ"
        body = [
            f"💰 Начало дня:   {menu_money(first_present(start.get('balance'), account.get('balance')), '$') or '$0.00'}",
            f"📊 P&L сегодня:  {menu_money(current_pnl, '$', signed=True) or '+$0.00'}",
            "",
            "🛡 ЛИМИТЫ",
            f"Дневной лимит:    {menu_money(limit, '$')}",
            f"Использовано:      {menu_money(used, '$')} ({used_pct}%)",
            f"Осталось:          {menu_money(remaining, '$')}",
            "",
            f"Статус: {status}",
            "",
            "📊 ПОЗИЦИИ",
            f"Открытых: {len(positions)}",
        ]
        return menu_message("📉 РИСК-МОНИТОР", body), menu_back_keyboard("menu_risk")
    except Exception:
        return menu_message("🔴 ОШИБКА РИСКА", ["Не удалось получить риск-данные."], True), menu_back_keyboard("menu_risk")


def estimate_position_risk(position: dict) -> Optional[float]:
    entry = safe_float(first_present(position.get("entry"), position.get("entry_price")))
    sl = safe_float(position.get("sl"))
    lot = safe_float(position.get("lot")) or 1.0
    if entry is None or sl is None:
        return None
    return abs(entry - sl) * abs(lot)


def render_bots_status() -> tuple[str, dict]:
    try:
        controls = acct.list_native_bot_controls(include_defaults=True)
        online = 0
        body = []
        for asset in MENU_ASSETS:
            control = next((c for c in controls if c.get("asset") == asset), None)
            if not control:
                body.append(f"{asset:<7} 🟡 Нет данных")
                continue
            active = control_enabled(control)
            if active:
                online += 1
            time_text = format_time(first_present(control.get("last_heartbeat_at"), control.get("updated_at"))) if first_present(control.get("last_heartbeat_at"), control.get("updated_at")) else ""
            status_text = "🟢 Активен" if active else "🔴 Выключен"
            body.append(f"{asset:<7} {status_text:<12} {time_text}".rstrip())
        body.extend(["", f"Онлайн:  {online}/5"])
        return menu_message("🤖 СТАТУС БОТОВ", body), menu_back_keyboard("menu_bots")
    except Exception:
        return menu_message("🔴 ОШИБКА БОТОВ", ["Не удалось получить статусы ботов."], True), menu_back_keyboard("menu_bots")


def handle_command(text: str, chat_id: Optional[str] = None) -> str:
    stripped = text.strip()
    if not stripped:
        return "Пустая команда."
    stripped = normalize_dashboard_button(stripped)
    if not stripped.startswith("/"):
        return handle_natural_language_command(stripped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID)

    parts = stripped.split()
    command = parts[0].lower()

    if command in ("/start", "/menu"):
        return render_command_center()[0]
    if command in ("/site", "/dashboard"):
        return site_message()
    if command == "/status":
        return render_command_center()[0]
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
    if command == "/backtest":
        return format_backtest_result(parts[1].upper() if len(parts) > 1 else "ALL")
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
    if command == "/enable_menu":
        return format_asset_menu("/enable", "Choose bot to enable")
    if command == "/disable_menu":
        return format_asset_menu("/disable", "Choose bot to stop")
    if command == "/performance_menu":
        return format_asset_menu("/performance", "Choose asset for performance")
    if command == "/screenshot_menu":
        return format_asset_menu("/last_screenshot", "Choose asset for last screenshot")
    if command == "/performance":
        return format_performance(parts[1] if len(parts) > 1 else "today")
    if command == "/performance_all":
        return format_performance("all")
    if command == "/symbols":
        return format_symbols()
    if command == "/journal":
        return format_journal(parts[1] if len(parts) > 1 else "today")
    if command == "/trades_today":
        return format_journal(parts[1] if len(parts) > 1 else "today")
    if command == "/trade_last":
        return format_trade_last()
    if command == "/trade":
        return format_trade_detail(parts[1] if len(parts) > 1 else "")
    if command in ("/last_screenshot", "/screenshot"):
        return send_last_screenshot(parts[1] if len(parts) > 1 else "")
    if command == "/bot_settings":
        return format_bot_settings(parts[1] if len(parts) > 1 else "")
    if command in ("/daily_report", "/daily_report_now"):
        return format_daily_report()
    if command in ("/bias", "/live_bias"):
        return render_live_bias_screen()[0]
    if command == "/signals":
        return render_signal_board()[0]
    if command == "/stats":
        return render_system_statistics_screen()[0]
    if command == "/sources":
        return render_signal_sources_screen()[0]
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
        return render_signal_risk_screen()[0]
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
    if isinstance(MAIN_KEYBOARD, dict):
        return MAIN_KEYBOARD
    return MAIN_KEYBOARD.to_dict()
def normalize_dashboard_button(text: str) -> str:
    mapping = {
        ACCOUNT_POSITIONS_BUTTON_TEXT: "/status",
        BIAS_BUTTON_TEXT: "/bias",
        SIGNALS_BUTTON_TEXT: "/signals",
        TRADES_BUTTON_TEXT: "/trades_today",
        ANALYTICS_BUTTON_TEXT: "/stats",
        "Core Status": "/status",
        "Trade Center": "/trades",
        "Market Intel": "/market_today",
        "Control Panel": "/settings",
        "Statistics": "/performance",
        "Last Screenshot": "/last_screenshot",
        SITE_BUTTON_TEXT: "/site",
        "Сайт": "/site",
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
    mapping.update(
        {
            "Включить бота": "/enable_menu",
            "Остановить бота": "/disable_menu",
            "📊 Статистика": "/performance_menu",
            "📒 Журнал": "/journal",
            "📸 Последний скрин": "/screenshot_menu",
            "Настройки": "/bot_settings",
            "Статус": "/status",
            "🧾 Сделки": "/trades_today",
            "Сделки": "/trades_today",
            SITE_BUTTON_TEXT: "/site",
            "Сайт": "/site",
        }
    )
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
    if control:
        asset = control.get("asset") or selector.upper()
        if enabled:
            return f"{asset} включён. Новые сделки разрешены."
        return f"{asset} остановлен. Новые сделки запрещены. Открытые позиции бот продолжит сопровождать."
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


def format_asset_menu(command: str, title: str) -> str:
    assets = ["NAS100", "SP500", "DJ30", "BTCUSD", "GER40"]
    lines = [title, ""]
    lines.extend(f"{asset}: {command} {asset}" for asset in assets)
    return "\n".join(lines)


def format_performance(arg: str = "today") -> str:
    period, selector = parse_performance_arg(arg)
    summary = acct.performance_summary(period, selector)
    label = period_label(period)
    items = summary.get("items", [])
    if selector and len(items) == 1:
        item = items[0]
        control = acct.get_native_bot_control(selector) or {}
        status = "ENABLED" if control_enabled(control or {"enabled": 1}) else "DISABLED"
        last_trade = acct.last_journal_trade(selector) or {}
        last_profit = fmt_pnl(last_trade.get("profit"), "€") if last_trade else "нет данных"
        last_reason = last_trade.get("close_reason") or last_trade.get("status") or "нет данных"
        return "\n".join(
            [
                f"📊 PERFORMANCE  {control.get('asset') or selector.upper()}",
                "",
                f"Status: {status}",
                f"Trades: {item.get('trades_count', 0)}",
                f"Wins/Losses: {item.get('wins', 0)} / {item.get('losses', 0)}",
                f"Winrate: {item.get('winrate', 0):.1f}%",
                f"Net PnL: {fmt_pnl(item.get('closed_pnl'), '€')}",
                f"Profit Factor: {fmt_pf(item.get('profit_factor'))}",
                f"Avg Win: {fmt_pnl(item.get('avg_win'), '€')}",
                f"Avg Loss: {fmt_pnl(item.get('avg_loss'), '€')}",
                f"Best: {fmt_pnl(item.get('best_trade'), '€')}",
                f"Worst: {fmt_pnl(item.get('worst_trade'), '€')}",
                f"Open failed today: {item.get('open_failed_count', 0)}",
                f"Last trade: {last_profit} / {last_reason}",
            ]
        )
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
    selector = None if str(arg or "").strip().lower() in ("", "today", "7d", "7", "30d", "30", "all", "alltime") else arg
    entries = acct.journal_entries(period, selector, limit=30)
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
        if selector:
            return f"Скрина по {label} пока нет."
        return f"Скриншотов по {label} пока нет."
    sent = send_telegram_photo(screenshot.get("file_path"), screenshot.get("caption") or "Последний скрин")
    if not sent:
        return "Скрин найден, но Telegram sendPhoto не прошёл."
    return "Последний скрин отправлен."


def format_bot_settings(selector: str = "") -> str:
    if not selector:
        controls = acct.list_native_bot_controls(include_defaults=True)
        lines = ["Настройки", ""]
        for control in controls:
            lines.append(f"{control.get('asset')}: {'ENABLED' if control_enabled(control) else 'DISABLED'}")
        return "\n".join(lines)
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
    account = acct.latest_account_snapshot() or {}
    pnl = acct.pnl_today()
    perf = acct.performance_summary("today")
    items = perf.get("items", [])
    totals = perf.get("totals", {})
    best = max(items, key=lambda item: item.get("closed_pnl", 0), default=None)
    worst = min(items, key=lambda item: item.get("closed_pnl", 0), default=None)
    currency = account_currency(account)
    lines = [
        "📊 DAILY TRADING REPORT",
        f"Berlin day: {datetime.now(BERLIN_TZ).strftime('%Y-%m-%d')}",
        "",
        "",
        "ACCOUNT",
        f"Balance: {fmt_money(account.get('balance'), currency)}",
        f"Equity: {fmt_money(account.get('equity'), currency)}",
        f"Daily closed PnL: {fmt_pnl(pnl.get('closed_pnl'), currency)}",
        f"Floating PnL: {fmt_pnl(pnl.get('floating_pnl'), currency)}",
        f"Total PnL: {fmt_pnl(pnl.get('total_pnl'), currency)}",
        "",
        "SYMBOLS",
    ]
    by_asset = {str(item.get("symbol") or "").replace(".r", "").upper(): item for item in items}
    for asset in ("NAS100", "SP500", "DJ30", "BTCUSD", "GER40"):
        aliases = [asset, "US500" if asset == "SP500" else asset, "GER40FT" if asset == "GER40" else asset]
        item = next((by_asset.get(alias) for alias in aliases if by_asset.get(alias)), None)
        if not item or not item.get("trades_count"):
            lines.append(f"{asset}: no trades")
        else:
            lines.append(
                f"{asset}: trades {item.get('trades_count', 0)} | pnl {fmt_pnl(item.get('closed_pnl'), currency)} | winrate {item.get('winrate', 0):.0f}%"
            )
    lines.extend(
        [
            "",
            f"BEST: {(best.get('symbol') + ' ' + fmt_pnl(best.get('closed_pnl'), currency)) if best else 'none'}",
            f"WORST: {(worst.get('symbol') + ' ' + fmt_pnl(worst.get('closed_pnl'), currency)) if worst else 'none'}",
            "",
            "ERRORS",
            f"Open failed: {totals.get('open_failed_count', 0)}",
            "Main reason: check native_trade_events messages",
            "",
            "CONCLUSION",
            f"- Best active bot today: {best.get('symbol') if best else 'none'}",
            f"- Weakest bot today: {worst.get('symbol') if worst else 'none'}",
            "- Suggested action: review weak symbols. Do not auto-change live risk.",
        ]
    )
    return "\n".join(lines)


def format_live_bias_latest() -> str:
    try:
        rows = bias_store.latest_live_bias()
    except Exception:
        rows = []
    if not rows:
        return "📈 БАЙЕС LIVE\n\nСнимка байеса пока нет."
    quality_values = [float(row.get("data_quality_score") or 0) for row in rows]
    risks = [row.get("risk") for row in rows]
    risk = "HIGH" if "HIGH" in risks else "MEDIUM" if "MEDIUM" in risks else "LOW"
    quality = round(sum(quality_values) / len(quality_values), 1) if quality_values else 0
    lines = ["📈 БАЙЕС LIVE", ""]
    for row in rows:
        lines.append(f"{dash_text(row.get('symbol')):<7} {dash_text(row.get('direction')):<5} {dash_text(row.get('confidence'))}% {dash_text(row.get('strength'))}")
    lines.extend(["", f"Риск: {risk}", f"Качество данных: {quality}%", f"Обновлено: {short_now()}"])
    return "\n".join(lines)


def format_signal_notification(signal: dict) -> str:
    verdict = signal.get("verdict")
    if verdict == "VALID_SIGNAL":
        title = "⚡ СКАЛЬП-СИГНАЛ"
    else:
        title = "👀 СИГНАЛ В НАБЛЮДЕНИИ"
    entry = format_entry_zone(signal)
    confirmations = signal.get("reasons") or []
    lines = [
        title,
        "",
        f"{dash_text(signal.get('symbol'))} | {dash_text(signal.get('direction'))} {dash_text(signal.get('score'))}%",
        f"Сетап: {dash_text(signal.get('setup'))}",
        "",
        f"Вход: {entry}",
        f"SL: {dash_text(signal.get('sl'))}",
        f"TP1: {dash_text(signal.get('tp1'))}",
        f"TP2: {dash_text(signal.get('tp2'))}",
        "",
        f"Риск: {dash_text(signal.get('risk_level'))}",
        f"Действует: {dash_text(signal.get('expiry_minutes'))} мин",
        "",
        "Подтверждения:" if verdict == "VALID_SIGNAL" else "Нужно:",
    ]
    if confirmations:
        lines.extend(f"✅ {dash_text(item)}" for item in confirmations[:6])
    else:
        lines.append("⬜ подтверждение ожидается")
    lines.extend(["", f"Источник: {dash_text(signal.get('source_name'))}"])
    return "\n".join(lines)


def format_signal_result_notification(signal: dict, evaluation: dict, source: dict | None = None) -> str:
    result = str(evaluation.get("result") or "pending").upper()
    icon = "✅" if result == "CORRECT" else "❌" if result == "WRONG" else "—"
    return "\n".join(
        [
            "📊 РЕЗУЛЬТАТ СИГНАЛА",
            "",
            f"{dash_text(signal.get('symbol'))} | {dash_text(signal.get('direction'))}",
            f"Сетап: {dash_text(signal.get('setup'))}",
            "",
            f"Оценка: {dash_text(signal.get('score'))}%",
            f"Результат {dash_text(evaluation.get('horizon'))}: {icon} {result}",
            f"Движение: {dash_text(evaluation.get('r_multiple'))}R",
            "",
            f"Источник: {dash_text(signal.get('source_name'))}",
            f"Trust обновлён: {dash_text((source or {}).get('trust_score'))}/100",
        ]
    )


def signal_line(signal: dict) -> str:
    return f"{dash_text(signal.get('symbol'))} {dash_text(signal.get('direction'))} {dash_text(signal.get('score'))}% | {dash_text(signal.get('setup'))}"


def rejected_line(signal: dict) -> str:
    reason = ", ".join(signal.get("rejection_reasons") or []) or signal.get("verdict")
    return f"{dash_text(signal.get('symbol'))} {dash_text(signal.get('direction'))} | {dash_text(reason)}"


def render_signal_detail_screen(signal_id: str) -> tuple[str, dict]:
    signal = signal_store.get_signal(signal_id, include_raw=False)
    if not signal:
        return "Сигнал не найден.", smob_inline_menu()
    return format_signal_notification(signal), smob_inline_menu()


def render_source_detail_screen(source_name: str) -> tuple[str, dict]:
    source = next((item for item in signal_store.source_reliability() if item.get("source_name") == source_name), None)
    if not source:
        return "Источник не найден.", smob_inline_menu()
    text = "\n".join([
        f"🧠 {dash_text(source.get('source_name'))}",
        "",
        f"Trust: {dash_text(source.get('trust_score'))}/100",
        f"Сигналы: {source.get('total_signals', 0)}",
        f"Точность: {dash_text(source.get('winrate'))}%",
        f"Avg R: {dash_text(source.get('average_R'))}",
    ])
    return text, smob_inline_menu()


def safe_call(func, fallback, *args):
    try:
        return func(*args)
    except Exception:
        return fallback


def safe_bias_accuracy_summary() -> dict:
    try:
        from .live_bias_accuracy import live_bias_accuracy

        data = live_bias_accuracy(limit=1000)
        overall = data.get("overall", {})
        by_symbol = data.get("by_symbol", {})
        ranked = []
        for symbol, payload in by_symbol.items():
            stats = (payload.get("all") or {})
            if stats.get("accuracy") is not None:
                ranked.append((symbol, stats["accuracy"]))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return {
            "30m": f"{dash_text((overall.get('30m') or {}).get('accuracy'))}%",
            "1h": f"{dash_text((overall.get('1h') or {}).get('accuracy'))}%",
            "best_symbol": ranked[0][0] if ranked else "—",
            "worst_symbol": ranked[-1][0] if ranked else "—",
        }
    except Exception:
        return {"30m": "—", "1h": "—", "best_symbol": "—", "worst_symbol": "—"}


def format_entry_zone(signal: dict) -> str:
    low = signal.get("entry_zone_low")
    high = signal.get("entry_zone_high")
    if low is not None and high is not None and low != high:
        return f"{dash_text(low)}–{dash_text(high)}"
    return dash_text(signal.get("entry"))


def fmt_signal_money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if number > 0 else "-" if number < 0 else ""
    return f"{sign}€{abs(number):.2f}"


def dash_text(value) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def short_now() -> str:
    return datetime.now(BERLIN_TZ).strftime("%H:%M")
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


# Russian command-center UI v2.  This block intentionally overrides the older
# menu renderers above while keeping all storage, signal, bias and MT5 logic
# untouched.
CONTROL_BUTTON_TEXT = "🎛 Пульт"
BIAS_BUTTON_TEXT = "📈 Bias"
SIGNALS_BUTTON_TEXT = "⚡ Сигналы"
TRADES_BUTTON_TEXT = "🧾 Сделки"
STATS_BUTTON_TEXT = "📊 Статистика"
RISK_BUTTON_TEXT = "🛡 Риск"
SOURCES_BUTTON_TEXT = "🧠 Sources"
SITE_BUTTON_TEXT = "🌐 Сайт"

MAIN_KEYBOARD_ROWS = [
    [CONTROL_BUTTON_TEXT, BIAS_BUTTON_TEXT],
    [SIGNALS_BUTTON_TEXT, TRADES_BUTTON_TEXT],
    [STATS_BUTTON_TEXT, RISK_BUTTON_TEXT],
    [SOURCES_BUTTON_TEXT, SITE_BUTTON_TEXT],
]
if ReplyKeyboardMarkup and KeyboardButton:
    MAIN_KEYBOARD = ReplyKeyboardMarkup(
        [[_reply_keyboard_button(text) for text in row] for row in MAIN_KEYBOARD_ROWS],
        resize_keyboard=True,
        is_persistent=True,
    )
else:
    MAIN_KEYBOARD = {
        "keyboard": [[_keyboard_button_payload(text) for text in row] for row in MAIN_KEYBOARD_ROWS],
        "resize_keyboard": True,
        "is_persistent": True,
    }

MENU_BUTTON_CALLBACKS = {
    CONTROL_BUTTON_TEXT: "refresh_center",
    BIAS_BUTTON_TEXT: "refresh_bias",
    SIGNALS_BUTTON_TEXT: "refresh_signals",
    TRADES_BUTTON_TEXT: "refresh_trades",
    STATS_BUTTON_TEXT: "refresh_stats",
    RISK_BUTTON_TEXT: "refresh_risk",
    SOURCES_BUTTON_TEXT: "refresh_sources",
    ACCOUNT_POSITIONS_BUTTON_TEXT: "refresh_center",
    ANALYTICS_BUTTON_TEXT: "refresh_stats",
}
MENU_BUTTON_PATTERN = (
    r"^(🎛 Пульт|📈 Bias|⚡ Сигналы|🧾 Сделки|📊 Статистика|🛡 Риск|🧠 Sources|🌐 Сайт|"
    r"📊 Счёт и позиции|📈 Байес|📉 Аналитика)$"
)
menu_message_handler = (
    MessageHandler(filters.TEXT & filters.Regex(MENU_BUTTON_PATTERN), handle_menu_button)
    if MessageHandler and filters
    else None
)


def _dash(value=None) -> str:
    return "—" if value is None or value == "" else str(value)


def _pct(value) -> str:
    number = safe_float(value)
    return "—" if number is None else f"{round(number)}%"


def _money_ru(value) -> str:
    number = safe_float(value)
    if number is None:
        return "—"
    sign = "+" if number > 0 else "-" if number < 0 else ""
    return f"{sign}€{abs(number):.2f}"


def _int_ru(value) -> str:
    number = safe_float(value)
    return "—" if number is None else str(int(number))


def _period_key(period: str) -> str:
    return {"today": "day", "day": "day", "week": "week", "month": "month", "all": "all"}.get(
        str(period or "day").lower(),
        "day",
    )


def period_to_store(period: str) -> str:
    return {"day": "today", "today": "today", "week": "7d", "month": "30d", "all": "all"}.get(
        str(period or "day").lower(),
        "today",
    )


def period_title(period: str) -> str:
    return {"day": "СЕГОДНЯ", "today": "СЕГОДНЯ", "week": "НЕДЕЛЯ", "month": "МЕСЯЦ", "all": "ВСЁ ВРЕМЯ"}.get(
        str(period or "day").lower(),
        str(period or "day").upper(),
    )


def _period_start_for_filter(period: str) -> Optional[datetime]:
    key = _period_key(period)
    now = datetime.now(BERLIN_TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "day":
        return today
    if key == "week":
        return now - timedelta(days=7)
    if key == "month":
        return now - timedelta(days=30)
    return None


def _row_time(row: dict):
    return first_present(
        row.get("timestamp"),
        row.get("close_time"),
        row.get("closed_at"),
        row.get("open_time"),
        row.get("opened_at"),
        row.get("time"),
        row.get("created_at"),
    )


def _row_in_ui_period(row: dict, period: str) -> bool:
    start = _period_start_for_filter(period)
    if start is None:
        return True
    parsed = parse_datetime(_row_time(row))
    if not parsed:
        return False
    return parsed.astimezone(BERLIN_TZ) >= start


def _status_ru(value) -> str:
    mapping = {
        "VALID_SIGNAL": "Подтверждён",
        "WATCH_ONLY": "Наблюдать",
        "WAIT_CONFIRMATION": "Наблюдать",
        "REJECTED": "Отклонён",
        "DUPLICATE": "Отклонён",
        "EXPIRED": "Истёк",
        "CORRECT": "Правильно",
        "WRONG": "Ошибка",
        "NEUTRAL": "Нейтрально",
        "LOW_SAMPLE_SIZE": "Мало данных",
        "not_enough_data": "Недостаточно данных",
        "pending": "ожидает",
        "correct": "правильно",
        "wrong": "ошибка",
        "neutral": "нейтрально",
        "open": "открыта",
        "win": "плюс",
        "loss": "минус",
        "breakeven": "BE",
        "SAFE": "SAFE",
        "WARNING": "WARNING",
        "NORMAL": "НОРМА",
    }
    return mapping.get(str(value or "").strip(), _dash(value))


def _strength_ru(value) -> str:
    return {"WEAK": "слабый", "MEDIUM": "средний", "STRONG": "сильный"}.get(str(value or "").upper(), _dash(value))


def _risk_ru(value) -> str:
    return {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"}.get(str(value or "").upper(), _dash(value))


def _side_ru(value) -> str:
    text = str(value or "").upper()
    if text in {"BUY", "LONG"}:
        return "LONG"
    if text in {"SELL", "SHORT"}:
        return "SHORT"
    return _dash(text)


def _signal_direction(signal: dict) -> str:
    return _side_ru(first_present(signal.get("direction"), signal.get("side")))


def _bias_direction(row: dict) -> str:
    direction = str(row.get("direction") or "").upper()
    if direction in {"LONG", "SHORT"}:
        return direction
    long_prob = safe_float(first_present(row.get("long_probability"), row.get("long_prob")))
    short_prob = safe_float(first_present(row.get("short_probability"), row.get("short_prob")))
    if long_prob is not None or short_prob is not None:
        return "LONG" if (long_prob or 0) >= (short_prob or 0) else "SHORT"
    return "LONG" if safe_float(row.get("confidence")) and safe_float(row.get("confidence")) >= 50 else "SHORT"


def _signal_line(signal: dict) -> str:
    setup = first_present(signal.get("setup"), signal.get("reason"), signal.get("comment"))
    return f"{_dash(signal.get('symbol'))} {_signal_direction(signal)} {_pct(signal.get('score'))} | {_dash(setup)}"


def _trade_time(row: dict) -> str:
    parsed = parse_datetime(_row_time(row))
    return parsed.astimezone(BERLIN_TZ).strftime("%H:%M") if parsed else "—"


def _period_buttons(prefix: str, active: str) -> list[list[tuple[str, str]]]:
    active = _period_key(active)
    labels = [("Сегодня", "day"), ("Неделя", "week"), ("Месяц", "month"), ("Всё время", "all")]
    row = []
    for label, key in labels:
        text = f"• {label}" if key == active else label
        row.append((text, f"{prefix}_period:{key}"))
    return [row]


def _dashboard_button_row() -> list[tuple[str, str]]:
    return [("🌐 Dashboard", "dashboard_url")]


def _inline_url_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return dashboard_url_keyboard(rows)


def _screen_keyboard(refresh: str, rows: Optional[list[list[tuple[str, str]]]] = None) -> dict:
    base = rows[:] if rows else []
    base.append([("🔄 Обновить", refresh), ("🌐 Dashboard", "dashboard_url")])
    return _inline_url_keyboard(base)


def smob_inline_menu() -> dict:
    return _inline_url_keyboard(
        [
            [("🔄 Обновить", "refresh_center"), ("📈 Bias", "refresh_bias")],
            [("⚡ Сигналы", "refresh_signals"), ("🛡 Риск", "refresh_risk")],
            [("🌐 Dashboard", "dashboard_url")],
        ]
    )


def site_message() -> str:
    return f"🌐 Dashboard\n{DASHBOARD_URL}"


def site_inline_keyboard() -> dict:
    return _inline_url_keyboard([[("🌐 Открыть Dashboard", "dashboard_url")]])


def menu_main_text() -> str:
    return "\n".join(["🎛 ЦЕНТР УПРАВЛЕНИЯ MT5", "", "Меню открыто. Выбери раздел ниже.", f"Обновлено: {short_now()}"])


def dashboard_keyboard() -> dict:
    if isinstance(MAIN_KEYBOARD, dict):
        return MAIN_KEYBOARD
    return MAIN_KEYBOARD.to_dict()


def _latest_event_text() -> str:
    events = safe_call(lambda: acct.native_trade_events(limit=1), [])
    if not events:
        return "—"
    event = events[0]
    return f"{_trade_time(event)} {_dash(event.get('event_type'))} {_dash(event.get('symbol'))}"


def render_command_center() -> tuple[str, dict]:
    pnl = safe_call(acct.native_pnl_today, {})
    storage = safe_call(acct.storage_health, {})
    account = safe_call(acct.latest_account_snapshot, None) or {}
    positions = safe_call(acct.current_native_positions, [])
    live_bias = safe_call(bias_store.latest_live_bias, [])
    signals = safe_call(lambda: signal_store.latest_signals(limit=300), [])
    native_available = safe_call(acct.native_data_available, False)
    valid = sum(1 for item in signals if item.get("verdict") == "VALID_SIGNAL")
    watch = sum(1 for item in signals if item.get("verdict") in {"WATCH_ONLY", "WAIT_CONFIRMATION"})
    rejected = sum(1 for item in signals if item.get("verdict") in {"REJECTED", "DUPLICATE", "EXPIRED"})
    storage_ok = bool(storage.get("db_file_exists") or storage.get("db_storage") == "render_persistent_disk")
    trade_count = first_present(pnl.get("trades_count"), pnl.get("closed_trades_count"), 0)
    lines = [
        "🎛 ЦЕНТР УПРАВЛЕНИЯ MT5",
        "",
        "Система: 🟢 Онлайн",
        f"MT5 Feed: {'🟢 Активен' if native_available else '🟡 Тихо'}",
        f"База: {'🟢 Persistent' if storage_ok else '🟡 Проверить'}",
        f"Живой Bias: {'🟢 Активен' if live_bias else '🟡 Нет снимка'}",
        f"Сигналы: {'🟢 Активны' if signals else '🟡 Нет данных'}",
        "",
        "Счёт:",
        f"Баланс: {_money_ru(account.get('balance'))}",
        f"Эквити: {_money_ru(account.get('equity'))}",
        f"PnL сегодня: {_money_ru(first_present(pnl.get('closed_pnl'), pnl.get('net_pnl'), 0))}",
        f"Открытый PnL: {_money_ru(sum(float_or_zero(p.get('profit')) for p in positions))}",
        "",
        "Торговля:",
        f"Открытые сделки: {len(positions)}",
        f"Закрыто сегодня: {trade_count}",
        f"Последнее событие: {_latest_event_text()}",
        "",
        "Сигналы:",
        f"Подтверждённые: {valid}",
        f"Наблюдать: {watch}",
        f"Отклонённые: {rejected}",
        "",
        "Риск: НОРМА",
        f"Обновлено: {short_now()}",
    ]
    return "\n".join(lines), _screen_keyboard(
        "refresh_center",
        [[("📈 Bias", "refresh_bias"), ("⚡ Сигналы", "refresh_signals")], [("🛡 Риск", "refresh_risk")]],
    )


def safe_bias_accuracy_summary() -> dict:
    try:
        from .live_bias_accuracy import live_bias_accuracy, live_bias_calibration

        data = live_bias_accuracy(limit=1000)
        calibration = live_bias_calibration(limit=1000)
        overall = data.get("overall", {})
        return {
            "30m": _pct((overall.get("30m") or {}).get("accuracy")),
            "1h": _pct((overall.get("1h") or {}).get("accuracy")),
            "2h": _pct((overall.get("2h") or {}).get("accuracy")),
            "4h": _pct((overall.get("4h") or {}).get("accuracy")),
            "best_symbol": _dash((calibration.get("best_symbol") or {}).get("group")),
            "worst_symbol": _dash((calibration.get("worst_symbol") or {}).get("group")),
        }
    except Exception:
        return {"30m": "—", "1h": "—", "2h": "—", "4h": "—", "best_symbol": "—", "worst_symbol": "—"}


def render_live_bias_screen() -> tuple[str, dict]:
    rows = safe_call(bias_store.latest_live_bias, [])
    if not rows:
        return "📈 ЖИВОЙ BIAS\n\nДанных Живого Bias пока нет.", _screen_keyboard(
            "refresh_bias",
            [[("📊 Точность", "bias_accuracy"), ("🧠 Калибровка", "bias_calibration")]],
        )
    quality = round(sum(float_or_zero(row.get("data_quality_score")) for row in rows) / max(len(rows), 1))
    risk = "HIGH" if any(str(row.get("risk")).upper() == "HIGH" for row in rows) else "MEDIUM" if any(str(row.get("risk")).upper() == "MEDIUM" for row in rows) else "LOW"
    lines = ["📈 ЖИВОЙ BIAS", ""]
    for row in rows[:8]:
        lines.append(
            f"{_dash(row.get('symbol')):<7} {_bias_direction(row):<5} {_pct(row.get('confidence')):<4} {_strength_ru(row.get('strength'))}"
        )
    accuracy = safe_bias_accuracy_summary()
    lines.extend(
        [
            "",
            f"Риск: {_risk_ru(risk)}",
            f"Качество данных: {_pct(quality)}",
            f"Обновлено: {short_now()}",
            "",
            "Точность:",
            f"30м: {accuracy.get('30m')}",
            f"1ч: {accuracy.get('1h')}",
            f"2ч: {accuracy.get('2h')}",
            f"4ч: {accuracy.get('4h')}",
            "",
            f"Лучший: {accuracy.get('best_symbol')}",
            f"Худший: {accuracy.get('worst_symbol')}",
        ]
    )
    return "\n".join(lines), _screen_keyboard(
        "refresh_bias",
        [[("📊 Точность", "bias_accuracy"), ("🧠 Калибровка", "bias_calibration")]],
    )


def render_signal_board(period: str = "day") -> tuple[str, dict]:
    period = _period_key(period)
    signals = [s for s in safe_call(lambda: signal_store.latest_signals(limit=500), []) if _row_in_ui_period(s, period)]
    valid = [s for s in signals if s.get("verdict") == "VALID_SIGNAL"][:5]
    watch = [s for s in signals if s.get("verdict") in {"WATCH_ONLY", "WAIT_CONFIRMATION"}][:5]
    rejected = [s for s in signals if s.get("verdict") in {"REJECTED", "DUPLICATE", "EXPIRED"}][:5]
    accuracy = safe_call(lambda: evaluate_signal_accuracy(limit=1000), {})
    horizons = accuracy.get("by_horizon", {})
    lines = [f"⚡ СИГНАЛЫ · {period_title(period)}", ""]
    if not signals:
        lines.append("Сигналы ещё не обработаны." if period == "day" else "За выбранный период данных нет.")
    else:
        lines.append("Подтверждённые:")
        lines.extend([_signal_line(item) for item in valid] or ["—"])
        lines.extend(["", "Наблюдать:"])
        lines.extend([_signal_line(item) for item in watch] or ["—"])
        lines.extend(["", "Отклонённые:"])
        lines.extend(
            [
                f"{_dash(item.get('symbol'))} {_signal_direction(item)} | {_dash(first_present(item.get('reason'), item.get('setup'), item.get('verdict')))}"
                for item in rejected
            ]
            or ["—"]
        )
        lines.extend(
            [
                "",
                "Точность:",
                f"15м: {_pct((horizons.get('15m') or {}).get('accuracy'))}",
                f"30м: {_pct((horizons.get('30m') or {}).get('accuracy'))}",
                f"60м: {_pct((horizons.get('60m') or {}).get('accuracy'))}",
                "",
                f"Обновлено: {short_now()}",
            ]
        )
    return "\n".join(lines), _screen_keyboard(
        "refresh_signals",
        _period_buttons("signals", period)
        + [
            [("✅ Подтверждённые", "signals_valid"), ("👀 Наблюдать", "signals_watch")],
            [("❌ Отклонённые", "signals_rejected"), ("📊 Точность", "signal_accuracy")],
        ],
    )


def _event_label(event_type) -> str:
    text = str(event_type or "").lower()
    mapping = {
        "opened": "OPEN",
        "tp1_closed": "TP1",
        "tp2_closed": "TP2",
        "tp3_closed": "TP3",
        "position_closed": "CLOSE",
        "closed_by_signal": "FULL CLOSE",
        "be_moved": "BE",
        "open_failed": "OPEN ERROR",
        "close_failed": "CLOSE ERROR",
    }
    return mapping.get(text, _dash(event_type).upper())


def render_processed_trades_signals(period: str = "day") -> tuple[str, dict]:
    period = _period_key(period)
    rows = safe_call(lambda: acct.get_trades_filtered(source="all", period=period_to_store(period), asset="ALL", limit=500), [])
    stats = safe_call(lambda: acct.get_stats_filtered(source="all", period=period_to_store(period), asset="ALL"), {})
    positions = safe_call(acct.current_native_positions, [])
    events = [e for e in safe_call(lambda: acct.native_trade_events(limit=100), []) if _row_in_ui_period(e, period)]
    signals = [s for s in safe_call(lambda: signal_store.latest_signals(limit=200), []) if _row_in_ui_period(s, period)]
    lines = [f"🧾 СДЕЛКИ · {period_title(period)}", "", "Итог:"]
    lines.extend(
        [
            f"PnL: {_money_ru(stats.get('total_pnl'))}",
            f"Сделок: {_int_ru(stats.get('total_trades'))}",
            f"Winrate: {_pct(stats.get('win_rate'))}",
            f"Открыто: {len(positions)}",
            "",
            "Открытые:",
        ]
    )
    if positions:
        for row in positions[:5]:
            tp = " ".join(
                [
                    f"TP1 {'✅' if row.get('tp1_done') else '⬜'}",
                    f"TP2 {'✅' if row.get('tp2_done') else '⬜'}",
                    f"TP3 {'✅' if row.get('tp3_done') else '⬜'}",
                ]
            )
            lines.extend([f"{_dash(row.get('symbol'))} {_side_ru(row.get('side'))}", tp, f"PnL: {_money_ru(row.get('profit'))}", ""])
    else:
        lines.append("Открытых сделок нет.")
        lines.append("")
    lines.append("Последние:")
    if events:
        for event in events[:6]:
            lines.append(f"{_trade_time(event)} {_event_label(event.get('event_type'))} {_dash(event.get('symbol'))} {_money_ru(event.get('profit'))}")
    elif rows:
        for row in rows[:6]:
            lines.append(f"{_trade_time(row)} {_dash(row.get('symbol'))} {_side_ru(row.get('side'))} {_money_ru(first_present(row.get('profit_money'), row.get('profit')))}")
    else:
        lines.append("За выбранный период сделок нет.")
    lines.extend(["", "Сигналы:"])
    if signals:
        for signal in signals[:4]:
            lines.extend(
                [
                    f"{_dash(signal.get('symbol'))} {_signal_direction(signal)}",
                    f"Score: {_pct(signal.get('score'))}",
                    f"Статус: {_status_ru(signal.get('verdict'))}",
                    f"Результат: {_status_ru('pending')}",
                    "",
                ]
            )
    else:
        lines.append("—")
    return "\n".join(lines).strip(), _screen_keyboard(
        "refresh_trades",
        _period_buttons("trades", period) + [[("🟢 MT5", "refresh_trades"), ("⚡ Сигналы", "refresh_signals")], [("📊 Результаты", "refresh_stats")]],
    )


def _event_counts(period: str) -> dict:
    events = [e for e in safe_call(lambda: acct.native_trade_events(limit=500), []) if _row_in_ui_period(e, period)]
    counts = {"TP1": 0, "TP2": 0, "SL": 0, "BE": 0}
    for event in events:
        label = _event_label(event.get("event_type"))
        if label in counts:
            counts[label] += 1
        if "SL" in label:
            counts["SL"] += 1
    return counts


def render_system_statistics_screen(period: str = "day") -> tuple[str, dict]:
    period = _period_key(period)
    stats = safe_call(lambda: acct.get_stats_filtered(source="all", period=period_to_store(period), asset="ALL"), {})
    rows = safe_call(lambda: acct.get_trades_filtered(source="all", period=period_to_store(period), asset="ALL", limit=500), [])
    accuracy = safe_call(lambda: evaluate_signal_accuracy(limit=1000), {})
    signals = [s for s in safe_call(lambda: signal_store.latest_signals(limit=500), []) if _row_in_ui_period(s, period)]
    sources = safe_call(signal_store.source_reliability, [])
    best_source = sources[0] if sources else {}
    bias_accuracy = safe_bias_accuracy_summary()
    horizons = accuracy.get("by_horizon", {})
    events = _event_counts(period)
    if not rows and not signals:
        body = ["За выбранный период статистики нет.", "Мало данных — импортируй историю MT5."]
    else:
        body = [
            "Торговля:",
            f"Сделок: {_int_ru(stats.get('total_trades'))}",
            f"Winrate: {_pct(stats.get('win_rate'))}",
            f"PnL: {_money_ru(stats.get('total_pnl'))}",
            f"PF: {_dash(stats.get('profit_factor'))}",
            f"Средний R: {_dash(stats.get('avg_r'))}R" if stats.get("avg_r") is not None else "Средний R: —",
            f"Лучшая: {_money_ru(stats.get('best_trade'))}",
            f"Худшая: {_money_ru(stats.get('worst_trade'))}",
            "",
            "События:",
            f"TP1: {events.get('TP1', 0)}",
            f"TP2: {events.get('TP2', 0)}",
            f"SL: {events.get('SL', 0)}",
            f"BE: {events.get('BE', 0)}",
            "",
            "Сигналы:",
            f"Обработано: {len(signals)}",
            f"Подтверждено: {sum(1 for s in signals if s.get('verdict') == 'VALID_SIGNAL')}",
            f"Наблюдать: {sum(1 for s in signals if s.get('verdict') in {'WATCH_ONLY', 'WAIT_CONFIRMATION'})}",
            f"Отклонено: {sum(1 for s in signals if s.get('verdict') in {'REJECTED', 'DUPLICATE', 'EXPIRED'})}",
            f"Точность 30м: {_pct((horizons.get('30m') or {}).get('accuracy'))}",
            "",
            "Bias:",
            f"Точность 30м: {bias_accuracy.get('30m')}",
            f"Точность 1ч: {bias_accuracy.get('1h')}",
            f"Лучший символ: {bias_accuracy.get('best_symbol')}",
            f"Худший символ: {bias_accuracy.get('worst_symbol')}",
            "",
            "Источники:",
            f"Лучший источник: {_dash(best_source.get('source_name'))}",
            f"Доверие: {_dash(best_source.get('trust_score'))}/100",
        ]
    return "\n".join([f"📊 СТАТИСТИКА · {period_title(period)}", "", *body]), _screen_keyboard(
        "refresh_stats",
        _period_buttons("stats", period)
        + [[("📈 Bias статистика", "bias_accuracy"), ("⚡ Сигналы", "refresh_signals")], [("🧠 Источники", "refresh_sources")]],
    )


def render_signal_risk_screen(period: str = "day") -> tuple[str, dict]:
    period = _period_key(period)
    stats = safe_call(lambda: acct.get_stats_filtered(source="all", period=period_to_store(period), asset="ALL"), {})
    storage = safe_call(acct.storage_health, {})
    risky = [s for s in safe_call(lambda: signal_store.latest_signals(limit=200), []) if s.get("risk_level") == "HIGH" and _row_in_ui_period(s, period)]
    storage_state = "SAFE" if storage.get("db_file_exists") else "WARNING"
    warnings = [f"⚠️ {_dash(item.get('symbol'))} сигнал {_risk_ru(item.get('risk_level'))}" for item in risky[:5]]
    if not safe_call(lambda: acct.get_trades_filtered(source="all", period=period_to_store(period), asset="ALL", limit=1), []):
        warnings.append("⚠️ История сделок пустая")
    lines = [
        f"🛡 КОНТРОЛЬ РИСКА · {period_title(period)}",
        "",
        "Статус: НОРМА" if not risky else "Статус: ВНИМАНИЕ",
        "",
        f"PnL сегодня: {_money_ru(stats.get('total_pnl'))}",
        "Открытый риск: —",
        "Худший SL damage: — дней",
        f"High-risk signals: {len(risky)}",
        "Риск Bias/Macro: —",
        "",
        "Предупреждения:",
        *(warnings or ["—"]),
        "",
        f"База: {storage_state}",
        f"История: {'OK' if storage.get('native_trade_journal', storage.get('counts', {}).get('native_trade_journal', 0)) else 'EMPTY'}",
    ]
    return "\n".join(lines), _screen_keyboard(
        "refresh_risk",
        _period_buttons("risk", period) + [[("⚡ Рискованные сигналы", "signals_risky"), ("🧠 Lab", "refresh_lab")]],
    )


def render_signal_sources_screen() -> tuple[str, dict]:
    sources = safe_call(signal_store.source_reliability, [])
    lines = ["🧠 ИСТОЧНИКИ СИГНАЛОВ", ""]
    if not sources:
        lines.append("Источники сигналов ещё не подключены.")
    for source in sources[:8]:
        lines.extend(
            [
                _dash(source.get("source_name")),
                f"Доверие: {_dash(source.get('trust_score'))}/100",
                f"Сигналов: {_dash(source.get('total_signals'))}",
                f"Точность: {_pct(source.get('winrate'))}",
                f"Средний R: {_dash(source.get('average_R'))}",
                "",
            ]
        )
    return "\n".join(lines).strip(), _screen_keyboard(
        "refresh_sources",
        [[("📊 Точность", "signal_accuracy"), ("⚡ Последние сигналы", "refresh_signals")]],
    )


def render_storage_screen() -> tuple[str, dict]:
    storage = safe_call(acct.storage_health, {})
    counts = storage.get("counts") or storage
    warnings = storage.get("warnings") or []
    path = first_present(storage.get("db_file_path"), storage.get("db_file"), "/var/data/bridge.db")
    size = first_present(storage.get("db_file_size_kb"), storage.get("db_size_kb"))
    lines = [
        "💾 СОСТОЯНИЕ БАЗЫ",
        "",
        f"DB: {'PERSISTENT 🟢' if storage.get('db_file_exists') or storage.get('db_storage') == 'render_persistent_disk' else 'WARNING 🟡'}",
        f"Путь: {_dash(path)}",
        f"Запись: {'да' if storage.get('db_file_writable', True) else 'нет'}",
        f"Размер: {_dash(size)} KB",
        "",
        "Данные:",
        f"Сделки: {_dash(counts.get('native_trade_journal'))}",
        f"Закрытые: {_dash(counts.get('native_mt5_closed_trades'))}",
        f"Native History: {_dash(counts.get('history_deals'))}",
        f"Account Snapshots: {_dash(counts.get('native_account_snapshots'))}",
        f"Screenshots: {_dash(counts.get('native_screenshots'))}",
        "",
        "Предупреждения:",
        ", ".join(warnings) if warnings else "none",
        "",
        f"Обновлено: {short_now()}",
    ]
    return "\n".join(lines), _screen_keyboard("refresh_storage")


def render_lab_screen() -> tuple[str, dict]:
    try:
        from .strategy_test_lab import build_strategy_lab_report

        report = build_strategy_lab_report(include_bias_filter=True)
    except Exception:
        report = {}
    risk = report.get("risk_damage_analytics") or {}
    trade_count = int(report.get("trade_count") or 0)
    lines = [
        "🧪 ЛАБОРАТОРИЯ СТРАТЕГИИ",
        "",
        f"Сделок: {trade_count}",
        f"Закрытых: {trade_count}",
        f"Sample: {_status_ru(report.get('sample_warning') or 'OK')}",
        f"Рекомендации: {'есть' if report.get('critical_problems') else 'недостаточно данных'}",
        "",
        "Риск:",
        f"SL damage: {_dash(risk.get('single_loss_damage_days'))}",
        f"PF: {_dash(risk.get('profit_factor'))}",
        f"Expectancy R: {_dash(risk.get('expectancy_R'))}",
        "",
        "Статус:",
        "requires_human_approval: да",
    ]
    if trade_count == 0:
        lines.extend(["", "Нет закрытой истории сделок. Импортируй историю MT5."])
    return "\n".join(lines), _screen_keyboard("refresh_lab", [[("📊 Данные", "refresh_storage")]])


def render_bias_accuracy_screen() -> tuple[str, dict]:
    accuracy = safe_bias_accuracy_summary()
    lines = [
        "📊 ТОЧНОСТЬ BIAS",
        "",
        f"30м: {accuracy.get('30m')}",
        f"1ч: {accuracy.get('1h')}",
        f"2ч: {accuracy.get('2h')}",
        f"4ч: {accuracy.get('4h')}",
        f"Лучший: {accuracy.get('best_symbol')}",
        f"Худший: {accuracy.get('worst_symbol')}",
    ]
    return "\n".join(lines), _screen_keyboard("bias_accuracy", [[("📈 Bias", "refresh_bias")]])


def render_signal_accuracy_screen() -> tuple[str, dict]:
    data = safe_call(lambda: evaluate_signal_accuracy(limit=1000), {})
    overall = data.get("overall", {}).get("all", {})
    lines = [
        "📊 ТОЧНОСТЬ СИГНАЛОВ",
        "",
        f"Сигналы: {data.get('signal_count', 0)}",
        f"Правильно: {overall.get('correct', 0)}",
        f"Ошибка: {overall.get('wrong', 0)}",
        f"Нейтрально: {overall.get('neutral', 0)}",
        f"Точность: {_pct(overall.get('accuracy'))}",
    ]
    return "\n".join(lines), _screen_keyboard("signal_accuracy", [[("⚡ Сигналы", "refresh_signals")]])


def render_menu_callback(data: str, chat_id: str) -> tuple[str, dict]:
    try:
        if data in {"refresh_center", "menu_main", "refresh_account_positions"}:
            return render_command_center()
        if data == "refresh_bias":
            return render_live_bias_screen()
        if data == "refresh_signals":
            return render_signal_board("day")
        if data.startswith("signals_period:"):
            return render_signal_board(data.split(":", 1)[1])
        if data in {"signals_valid", "signals_watch", "signals_rejected", "signals_risky"}:
            return render_signal_board("day")
        if data in {"refresh_trades", "refresh_processed_trades", "menu_processed_trades"}:
            return render_processed_trades_signals("day")
        if data.startswith("trades_period:"):
            return render_processed_trades_signals(data.split(":", 1)[1])
        if data.startswith("trades_p_"):
            return render_processed_trades_signals(data.replace("trades_p_", "", 1))
        if data in {"refresh_stats", "refresh_analytics"}:
            return render_system_statistics_screen("day")
        if data.startswith("stats_period:"):
            return render_system_statistics_screen(data.split(":", 1)[1])
        if data.startswith("stats_p_"):
            return render_system_statistics_screen(data.replace("stats_p_", "", 1))
        if data == "refresh_risk":
            return render_signal_risk_screen("day")
        if data.startswith("risk_period:"):
            return render_signal_risk_screen(data.split(":", 1)[1])
        if data == "refresh_sources":
            return render_signal_sources_screen()
        if data == "refresh_storage":
            return render_storage_screen()
        if data == "refresh_lab":
            return render_lab_screen()
        if data == "bias_accuracy":
            return render_bias_accuracy_screen()
        if data == "bias_calibration":
            return "🧠 КАЛИБРОВКА BIAS\n\nРекомендации доступны в Dashboard.\nТребуется ручное подтверждение: да", _screen_keyboard("refresh_bias")
        if data == "signal_accuracy":
            return render_signal_accuracy_screen()
        if data.startswith("signal_details:"):
            return render_signal_detail_screen(data.split(":", 1)[1])
        if data.startswith("source_details:"):
            return render_source_detail_screen(data.split(":", 1)[1])
        if data == "dashboard_url":
            return site_message(), site_inline_keyboard()
        return render_command_center()
    except Exception:
        return "🔴 ОШИБКА\n\nДанные временно недоступны.", _screen_keyboard("refresh_center")


def _command_to_callback(command: str) -> Optional[str]:
    return {
        "/status": "refresh_center",
        "/bias": "refresh_bias",
        "/live_bias": "refresh_bias",
        "/signals": "refresh_signals",
        "/trades": "refresh_trades",
        "/stats": "refresh_stats",
        "/risk": "refresh_risk",
        "/sources": "refresh_sources",
        "/storage": "refresh_storage",
        "/lab": "refresh_lab",
    }.get(command)


async def show_command_screen(update, context):
    if not getattr(update, "message", None):
        return
    command = str(update.message.text or "").split()[0].lower()
    if command in ("/site", "/dashboard"):
        await update.message.reply_text(site_message(), reply_markup=ptb_reply_markup(site_inline_keyboard()))
        return
    callback_data = _command_to_callback(command)
    if callback_data:
        chat_id = str(getattr(update.message, "chat_id", "") or "")
        text, keyboard = render_menu_callback(callback_data, chat_id)
        await update.message.reply_text(text, reply_markup=ptb_reply_markup(keyboard))


async def start(update, context):
    if getattr(update, "message", None):
        await update.message.reply_text(menu_main_text(), reply_markup=ptb_reply_markup(MAIN_KEYBOARD))


async def _reply_menu_section(update, callback_data: str) -> None:
    if not getattr(update, "message", None):
        return
    chat_id = str(update.message.chat_id)
    text, keyboard = render_menu_callback(callback_data, chat_id)
    await update.message.reply_text(text, reply_markup=ptb_reply_markup(keyboard))


async def handle_menu_button(update, context):
    text = update.message.text
    if text in (SITE_BUTTON_TEXT, "Сайт"):
        await show_site(update, context)
        return
    callback_data = MENU_BUTTON_CALLBACKS.get(text)
    if callback_data:
        chat_id = getattr(update.message, "chat_id", None)
        rendered_text, keyboard = render_menu_callback(callback_data, str(chat_id or ""))
        await update.message.reply_text(rendered_text, reply_markup=ptb_reply_markup(keyboard))


def handle_telegram_update(update: dict) -> bool:
    try:
        callback = update.get("callback_query") or {}
        if callback:
            answer_callback_query(str(callback.get("id") or ""))
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id")) if chat.get("id") is not None else None
            if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
                return True
            text, keyboard = render_menu_callback(str(callback.get("data") or ""), chat_id or "")
            menu_send(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, text, keyboard, message.get("message_id"))
            return True

        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else None
        text = str(message.get("text") or "").strip()
        if not chat_id or not text:
            return False
        if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
            return True

        command = text.split()[0].lower()
        if command in ("/start", "/menu") or text.lower() == "меню":
            user_state[chat_id] = {"screen": "center"}
            menu_send(chat_id, menu_main_text(), dashboard_keyboard())
            return True
        if text in (SITE_BUTTON_TEXT, "Сайт") or command in ("/site", "/dashboard"):
            menu_send(chat_id, site_message(), site_inline_keyboard())
            return True
        callback_data = _command_to_callback(command) or MENU_BUTTON_CALLBACKS.get(text)
        if callback_data:
            text_out, keyboard = render_menu_callback(callback_data, chat_id)
            menu_send(chat_id, text_out, keyboard)
            return True
        if command == "/backtest":
            if len(text.split()) > 1:
                text_out, keyboard = render_backtest_result(text.split()[1].upper())
            else:
                text_out, keyboard = render_backtest_selector()
            menu_send(chat_id, text_out, keyboard)
            return True
        return False
    except Exception:
        try:
            menu_send(config.TELEGRAM_ADMIN_CHAT_ID, "🔴 ОШИБКА\n\nНе удалось обработать запрос.", _screen_keyboard("refresh_center"))
        except Exception:
            pass
        return True


def handle_command(text: str, chat_id: Optional[str] = None) -> str:
    stripped = text.strip()
    if not stripped:
        return "Пустая команда."
    stripped = normalize_dashboard_button(stripped)
    if not stripped.startswith("/"):
        return handle_natural_language_command(stripped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
    command = stripped.split()[0].lower()
    if command in ("/start", "/menu"):
        return menu_main_text()
    if command in ("/site", "/dashboard"):
        return site_message()
    callback_data = _command_to_callback(command)
    if callback_data:
        return render_menu_callback(callback_data, chat_id or "")[0]
    if command == "/backtest":
        parts = stripped.split()
        return format_backtest_result(parts[1].upper() if len(parts) > 1 else "ALL")
    return "Команда не распознана. Используй /menu."


def normalize_dashboard_button(text: str) -> str:
    mapping = {
        CONTROL_BUTTON_TEXT: "/status",
        BIAS_BUTTON_TEXT: "/bias",
        SIGNALS_BUTTON_TEXT: "/signals",
        TRADES_BUTTON_TEXT: "/trades",
        STATS_BUTTON_TEXT: "/stats",
        RISK_BUTTON_TEXT: "/risk",
        SOURCES_BUTTON_TEXT: "/sources",
        SITE_BUTTON_TEXT: "/dashboard",
        ACCOUNT_POSITIONS_BUTTON_TEXT: "/status",
        ANALYTICS_BUTTON_TEXT: "/stats",
        "Сайт": "/dashboard",
        "Статистика": "/stats",
        "Сделки": "/trades",
    }
    return mapping.get(text, text)


menu_message_handler = (
    MessageHandler(filters.TEXT & filters.Regex(MENU_BUTTON_PATTERN), handle_menu_button)
    if MessageHandler and filters
    else None
)
regular_command_handler = (
    CommandHandler(["status", "bias", "live_bias", "signals", "trades", "stats", "risk", "sources", "storage", "lab"], show_command_screen)
    if CommandHandler
    else None
)


def register_menu_handlers(app) -> None:
    if menu_message_handler:
        app.add_handler(menu_message_handler, group=-1)
    if start_command_handler:
        app.add_handler(start_command_handler, group=-1)
    if site_command_handler:
        app.add_handler(site_command_handler, group=-1)
    if regular_command_handler:
        app.add_handler(regular_command_handler, group=-1)
    if menu_callback_query_handler:
        app.add_handler(menu_callback_query_handler)
