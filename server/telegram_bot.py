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
from .database import db

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import CallbackQueryHandler
except Exception:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    CallbackQueryHandler = None


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
    payload = event.model_dump(mode="json", exclude={"secret"})
    # Enrich with DB trade data so format function has accumulated profit, timestamps, etc.
    trade = acct.get_trade_for_notification(event)
    if trade:
        if event_type in {"position_closed", "closed_by_signal"}:
            # Use accumulated total profit (TP1+TP2+close), not just remaining lot
            if trade.get("profit") is not None:
                payload["profit"] = trade["profit"]
            payload.setdefault("opened_at", trade.get("opened_at"))
            payload.setdefault("exit_price", trade.get("exit_price"))
            payload["tp1_done"] = bool(trade.get("tp1_done"))
            payload["tp2_done"] = bool(trade.get("tp2_done"))
            payload["tp3_done"] = bool(trade.get("tp3_done"))
            payload["be_done"] = bool(trade.get("be_done"))
        elif event_type in {"tp1_closed", "tp2_closed", "tp3_closed"}:
            # Keep event.profit as this TP's individual profit
            # Add accumulated running total from the now-updated active trade
            payload["accumulated_profit"] = trade.get("profit")
            payload.setdefault("entry", trade.get("entry"))
            payload.setdefault("sl", trade.get("sl"))
            payload.setdefault("lot", trade.get("lot"))
    notification = format_native_mt5_event_message(payload)
    return send_telegram_message(notification) if notification else False


def format_native_mt5_event_message(event: dict) -> str:
    D = "━━━━━━━━━━━━━━━━━━"
    event = event or {}
    event_type = str(event.get("event_type") or "").strip().lower()
    symbol = event.get("symbol") or "n/a"
    side_raw = event.get("side")

    def _side(v) -> str:
        s = str(v or "").strip().lower()
        return "BUY" if s == "buy" else "SELL" if s == "sell" else str(v or "").upper()

    def _price(v) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v)

    def _money(v) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)
        if n > 0:
            return f"+{n:.2f} €"
        if n < 0:
            return f"–{abs(n):.2f} €"
        return "0.00 €"

    def _lot(v) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            return f"{float(v):.2f} lot"
        except (TypeError, ValueError):
            return str(v)

    def _pts(entry, level) -> Optional[str]:
        if entry is None or level is None:
            return None
        try:
            diff = float(level) - float(entry)
            sign = "+" if diff >= 0 else "–"
            return f"({sign}{abs(diff):.2f} pts)"
        except (TypeError, ValueError):
            return None

    def _risk(v) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            return f"~€{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v)

    def _r(profit_val, risk_val) -> Optional[str]:
        if profit_val is None or risk_val is None:
            return None
        try:
            r = float(profit_val) / float(risk_val)
            sign = "+" if r >= 0 else ""
            return f"({sign}{r:.2f}R)"
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _hhmm(v) -> Optional[str]:
        if not v:
            return None
        m = re.search(r"(\d{2}):(\d{2})", str(v))
        return f"{m.group(1)}:{m.group(2)}" if m else None

    def _duration(opened_at, closed_at) -> Optional[str]:
        if not opened_at or not closed_at:
            return None
        try:
            fmt = "%Y-%m-%dT%H:%M:%S" if "T" in str(opened_at) else "%Y-%m-%d %H:%M:%S"
            o = datetime.strptime(str(opened_at)[:19], fmt)
            c = datetime.strptime(str(closed_at)[:19], fmt)
            mins = int((c - o).total_seconds() / 60)
            if mins < 0:
                mins = 0
            if mins >= 60:
                h, m = divmod(mins, 60)
                return f"{h}ч {m}мин"
            return f"{mins} мин"
        except (ValueError, TypeError):
            return None

    def _remaining(closed_pct) -> Optional[str]:
        if closed_pct is None:
            return None
        try:
            pct = float(closed_pct)
            if 0 < pct <= 1:
                pct *= 100
            rem = round(100 - pct)
            return f"{rem}% позиции в рынке"
        except (TypeError, ValueError):
            return None

    side = _side(side_raw)
    risk_val = event.get("risk") or event.get("initial_risk") or event.get("risk_amount")

    if event_type == "opened":
        entry = event.get("entry")
        sl = event.get("sl")
        lines = [
            "🟢 СДЕЛКА ОТКРЫТА",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if entry is not None:
            lines.append(f"Entry:    {_price(entry)}")
        if sl is not None:
            sl_pts = _pts(entry, sl)
            lines.append(f"SL:       {_price(sl)}  {sl_pts}" if sl_pts else f"SL:       {_price(sl)}")
        lines.append(D)
        for tp_key in ("tp1", "tp2", "tp3"):
            tp_val = event.get(tp_key)
            if tp_val is not None:
                tp_pts = _pts(entry, tp_val)
                label = tp_key.upper()
                lines.append(f"{label}:     {_price(tp_val)}   {tp_pts}" if tp_pts else f"{label}:     {_price(tp_val)}")
        r_line = _risk(risk_val)
        if r_line:
            lines.append(f"Risk:    {r_line}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        lines.append(f"🕐 {time_str}  |  Native MT5" if time_str else "🕐 Native MT5")
        return "\n".join(lines)

    if event_type == "tp1_closed":
        tp_price = event.get("tp1") or event.get("exit_price") or event.get("current_price")
        profit = event.get("profit")
        accumulated = event.get("accumulated_profit")
        r_str = _r(profit, risk_val)
        lines = [
            "🎯 TP1 ВЗЯТ",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if tp_price is not None:
            lines.append(f"TP1:     {_price(tp_price)}  ✅")
        profit_line = _money(profit)
        if profit_line:
            lines.append(f"Profit:  {profit_line}  {r_str}" if r_str else f"Profit:  {profit_line}")
        lines.append("SL → BE ✅  (позиция защищена)")
        rem = _remaining(event.get("closed_percent"))
        if rem:
            lines.append(f"Остаток: {rem}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        if time_str:
            lines.append(f"🕐 {time_str}")
        return "\n".join(lines)

    if event_type == "tp2_closed":
        tp_price = event.get("tp2") or event.get("exit_price") or event.get("current_price")
        profit = event.get("profit")
        accumulated = event.get("accumulated_profit")
        r_str = _r(profit, risk_val)
        r_accum = _r(accumulated, risk_val)
        lines = [
            "🎯🎯 TP2 ВЗЯТ",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if tp_price is not None:
            lines.append(f"TP2:     {_price(tp_price)}  ✅")
        profit_line = _money(profit)
        if profit_line:
            lines.append(f"Profit:  {profit_line}  {r_str}" if r_str else f"Profit:  {profit_line}")
        if accumulated is not None:
            accum_line = _money(accumulated)
            if accum_line:
                lines.append(f"Суммарно: {accum_line}  {r_accum}" if r_accum else f"Суммарно: {accum_line}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        if time_str:
            lines.append(f"🕐 {time_str}")
        return "\n".join(lines)

    if event_type == "tp3_closed":
        tp_price = event.get("tp3") or event.get("exit_price") or event.get("current_price")
        profit = event.get("profit")
        accumulated = event.get("accumulated_profit")
        r_str = _r(profit, risk_val)
        r_accum = _r(accumulated, risk_val)
        lines = [
            "🎯🎯🎯 TP3 ВЗЯТ",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if tp_price is not None:
            lines.append(f"TP3:     {_price(tp_price)}  ✅")
        profit_line = _money(profit)
        if profit_line:
            lines.append(f"Profit:  {profit_line}  {r_str}" if r_str else f"Profit:  {profit_line}")
        if accumulated is not None:
            accum_line = _money(accumulated)
            if accum_line:
                lines.append(f"Суммарно: {accum_line}  {r_accum}" if r_accum else f"Суммарно: {accum_line}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        if time_str:
            lines.append(f"🕐 {time_str}")
        return "\n".join(lines)

    if event_type == "be_moved":
        entry = event.get("entry")
        lines = [
            "🛡 БЕЗУБЫТОК АКТИВИРОВАН",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if entry is not None:
            lines.append(f"BE: {_price(entry)}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        if time_str:
            lines.append(f"🕐 {time_str}")
        return "\n".join(lines)

    if event_type in {"position_closed", "closed_by_signal"}:
        profit = event.get("profit")
        try:
            profit_value = float(profit or 0)
        except (TypeError, ValueError):
            profit_value = 0.0
        win = profit_value > 0
        header = "✅ СДЕЛКА ЗАКРЫТА  —  ПРОФИТ" if win else "❌ СДЕЛКА ЗАКРЫТА  —  УБЫТОК"
        lot_str = _lot(event.get("lot"))
        header2_parts = [f"📊 {symbol}", side]
        if lot_str:
            header2_parts.append(lot_str)
        lines = [header, D, "  |  ".join(header2_parts)]
        entry = event.get("entry")
        exit_p = event.get("exit_price") or event.get("current_price")
        if entry is not None:
            lines.append(f"Entry:    {_price(entry)}")
        if exit_p is not None:
            lines.append(f"Exit:     {_price(exit_p)}")
        lines.append(D)
        pnl_line = _money(profit)
        if pnl_line:
            lines.append(f"P&L:     {pnl_line}")
        r_str = _r(profit_value, risk_val)
        if r_str:
            lines.append(f"R:       {r_str.strip('()')}")
        dur = _duration(event.get("opened_at"), event.get("time"))
        if dur:
            lines.append(f"Время:   {dur}")
        tp_flags = []
        if event.get("tp1_done"):
            tp_flags.append("TP1 ✅")
        if event.get("tp2_done"):
            tp_flags.append("TP2 ✅")
        if event.get("tp3_done"):
            tp_flags.append("TP3 ✅")
        if tp_flags:
            lines.append("  ".join(tp_flags))
        elif not win:
            lines.append("SL сработал")
        lines.append(D)
        try:
            daily = acct.native_pnl_today()
            d_pnl = _money(daily.get("closed_pnl"))
            d_wins = daily.get("wins", 0)
            d_losses = daily.get("losses", 0)
            if d_pnl:
                trend = "📈" if profit_value > 0 else "📉"
                lines.append(f"{trend} Итог дня:  {d_pnl}  |  {d_wins}W / {d_losses}L")
        except Exception:
            pass
        return "\n".join(lines)

    if event_type in {"open_failed", "close_failed", "rejected", "close_rejected", "error"}:
        reason = event.get("message") or event.get("reason")
        lines = [
            "⚠️ ВХОД НЕ ВЫПОЛНЕН",
            D,
            f"📊 {symbol}  |  {side}",
        ]
        if reason:
            lines.append(f"Причина: {reason}")
        lines.append(D)
        time_str = _hhmm(event.get("time"))
        if time_str:
            lines.append(f"🕐 {time_str}")
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

    def _money_caption(v) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)
        if n > 0:
            return f"+{n:.2f} €"
        if n < 0:
            return f"–{abs(n):.2f} €"
        return "0.00 €"

    def _r_caption(profit_val, risk_val) -> Optional[str]:
        if profit_val is None or risk_val is None:
            return None
        try:
            r = float(profit_val) / float(risk_val)
            return f"{r:.2f}R"
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    if event_type == "opened":
        entry = event.get("entry")
        parts = [symbol, side]
        if entry is not None:
            parts.append(f"Entry {fmt_native_price(entry)}")
        return " | ".join(parts)

    if event_type == "tp1_closed":
        profit = event.get("profit")
        parts = [symbol, side, "TP1 ✅"]
        pnl = _money_caption(profit)
        if pnl:
            parts.append(pnl)
        return " | ".join(parts)

    if event_type == "tp2_closed":
        profit = event.get("profit")
        parts = [symbol, side, "TP2 ✅"]
        pnl = _money_caption(profit)
        if pnl:
            parts.append(pnl)
        return " | ".join(parts)

    if event_type == "tp3_closed":
        profit = event.get("profit")
        parts = [symbol, side, "TP3 ✅"]
        pnl = _money_caption(profit)
        if pnl:
            parts.append(pnl)
        return " | ".join(parts)

    if event_type == "be_moved":
        return " | ".join([symbol, side, "SL → BE"])

    if event_type in {"position_closed", "closed_by_signal"}:
        profit = event.get("profit")
        risk_val = event.get("risk") or event.get("initial_risk") or event.get("risk_amount")
        pnl = _money_caption(profit)
        parts = [symbol, side]
        if pnl:
            parts.append(pnl)
        r = _r_caption(profit, risk_val)
        if r:
            parts.append(r)
        return " | ".join(parts)

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


async def _ptb_callback_router(update, context):
    query = getattr(update, "callback_query", None)
    if query:
        await query.answer()


menu_callback_query_handler = CallbackQueryHandler(_ptb_callback_router) if CallbackQueryHandler else None


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
    sign = "+" if signed and number > 0 else "-" if signed and number < 0 else ""
    return f"{sign}{currency}{abs(number):,.{decimals}f}"


def menu_number(value, decimals: int = 0, signed: bool = False) -> Optional[str]:
    number = safe_float(value)
    if number is None:
        return None
    sign = "+" if signed and number > 0 else "-" if signed and number < 0 else ""
    return f"{sign}{abs(number):,.{decimals}f}"


def menu_pct(value, decimals: int = 0) -> Optional[str]:
    number = safe_float(value)
    if number is None:
        return None
    return f"{number:.{decimals}f}%"


def menu_duration(start, end) -> Optional[str]:
    opened = parse_datetime(start)
    closed = parse_datetime(end)
    if not opened or not closed:
        return None
    minutes = max(0, int((closed - opened).total_seconds() // 60))
    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f"{hours}ч {mins}м" if mins else f"{hours}ч"
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


def menu_line(label: str, value: Optional[str], width: int = 14) -> Optional[str]:
    if value is None or value == "":
        return None
    return f"{label:<{width}} {value}"


def menu_message(title: str, body: list[str], footer: bool = True) -> str:
    lines = [title, MENU_DIVIDER, ""]
    lines.extend(line for line in body if line is not None)
    if footer:
        lines.extend(["", menu_timestamp()])
    return "\n".join(lines).strip()


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


def main_menu_keyboard() -> dict:
    return inline_keyboard(
        [
            [("📊 Статус", "menu_status"), ("📋 Сделки", "menu_trades")],
            [("📈 Статистика", "menu_stats"), (" Действия", "menu_actions")],
            [("🌍 Рынок", "menu_market"), ("🏆 Рекорды", "menu_records")],
            [("📉 Риск", "menu_risk"), ("🤖 Боты", "menu_bots")],
        ]
    )


def menu_back_keyboard(refresh: str, back: Optional[str] = None) -> dict:
    row = [("🔁 Обновить", refresh)]
    if back:
        row.append((" Назад", back))
    row.append((" Меню", "menu_main"))
    return inline_keyboard([row])


def menu_main_text() -> str:
    return "\n".join([" TRADING CONTROL", MENU_DIVIDER, "", "Система управления торговыми ботами", menu_timestamp()])


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
            user_state[chat_id] = {"screen": "main"}
            menu_send(chat_id, menu_main_text(), main_menu_keyboard())
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
        if data == "menu_main":
            user_state[chat_id] = {"screen": "main"}
            return menu_main_text(), main_menu_keyboard()
        if data == "menu_status":
            return render_menu_status()
        if data == "menu_trades":
            user_state[chat_id] = {"screen": "trades_period"}
            return render_trades_period()
        if data.startswith("trades_"):
            period = data.replace("trades_", "", 1)
            user_state[chat_id] = {"screen": "trades_result", "period": period}
            return render_trades_result(period)
        if data == "menu_stats":
            user_state[chat_id] = {"screen": "stats_period"}
            return render_stats_period()
        if data.startswith("stats_period_"):
            period = data.replace("stats_period_", "", 1)
            user_state[chat_id] = {"screen": "stats_asset", "period": period}
            return render_stats_asset(period)
        if data.startswith("stats_asset_"):
            asset = data.replace("stats_asset_", "", 1)
            period = user_state.get(chat_id, {}).get("period", "day")
            user_state[chat_id] = {"screen": "stats_result", "period": period, "asset": asset}
            return render_stats_result(period, asset)
        if data == "stats_refresh":
            state = user_state.get(chat_id, {})
            return render_stats_result(state.get("period", "day"), state.get("asset", "all"))
        if data == "stats_back_asset":
            period = user_state.get(chat_id, {}).get("period", "day")
            return render_stats_asset(period)
        if data == "menu_actions":
            user_state[chat_id] = {"screen": "actions"}
            return render_actions_menu()
        if data in {"action_enable", "action_disable"}:
            action = "enable" if data == "action_enable" else "disable"
            user_state[chat_id] = {"screen": "action_select", "action": action}
            return render_action_select(action)
        if data in {"action_enable_all", "action_disable_all"}:
            action = "enable_all" if data == "action_enable_all" else "disable_all"
            user_state[chat_id] = {"screen": "action_confirm", "action": action, "asset": "Все"}
            return render_action_confirm(action, "Все")
        if data.startswith("action_bot_"):
            asset = data.replace("action_bot_", "", 1)
            action = user_state.get(chat_id, {}).get("action", "enable")
            user_state[chat_id] = {"screen": "action_confirm", "action": action, "asset": asset}
            return render_action_confirm(action, asset)
        if data == "action_confirm":
            state = user_state.get(chat_id, {})
            return execute_action(state.get("action", "enable"), state.get("asset", "NAS100"))
        if data == "action_report":
            return render_daily_report_menu()
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
        currency = "$"
        trades = int(pnl.get("trades_count") or pnl.get("closed_trades_count") or 0)
        wins = int(pnl.get("wins") or 0)
        losses = int(pnl.get("losses") or 0)
        winrate = round((wins / trades) * 100) if trades else 0
        body = [
            "💰 АККАУНТ",
            menu_line("💰 Баланс:", menu_money(account.get("balance"), currency)),
            menu_line("📊 Equity:", menu_money(account.get("equity"), currency)),
            menu_line("📉 Margin:", menu_money(account.get("margin"), currency)),
            menu_line("Свободно:", menu_money(account.get("free_margin"), currency)),
            "",
            "📅 СЕГОДНЯ",
            menu_line("P&L:", menu_money(pnl.get("closed_pnl", pnl.get("net_pnl")), currency, signed=True)),
            menu_line("Сделок:", f"{trades}  ({wins}W / {losses}L)"),
            menu_line("Винрейт:", f"{winrate}%"),
            "",
            "🤖 БОТЫ",
        ]
        bot_parts = []
        for control in controls:
            if control.get("asset") in MENU_ASSETS:
                bot_parts.append(f"{control.get('asset'):<7} {'🟢' if control_enabled(control) else '🔴'}")
        for index in range(0, len(bot_parts), 2):
            body.append("  ".join(bot_parts[index:index + 2]))
        return menu_message("📊 СТАТУС АККАУНТА", body), menu_back_keyboard("menu_status")
    except Exception:
        return menu_message("🔴 ОШИБКА СТАТУСА", ["Не удалось получить данные аккаунта."], True), menu_back_keyboard("menu_status")


def render_trades_period() -> tuple[str, dict]:
    text = menu_message("📋 СДЕЛКИ  ВЫБЕРИ ПЕРИОД", [], True)
    keyboard = inline_keyboard(
        [
            [("Сегодня", "trades_today"), ("Вчера", "trades_yesterday"), ("Неделя", "trades_week")],
            [(" Меню", "menu_main")],
        ]
    )
    return text, keyboard


def period_to_store(period: str) -> str:
    return {"today": "today", "yesterday": "yesterday", "week": "7d", "day": "today", "month": "30d"}.get(period, "today")


def period_title(period: str) -> str:
    return {"today": "СЕГОДНЯ", "yesterday": "ВЧЕРА", "week": "НЕДЕЛЯ", "day": "ДЕНЬ", "month": "МЕСЯЦ"}.get(period, period.upper())


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


def render_trades_result(period: str) -> tuple[str, dict]:
    try:
        rows = [r for r in menu_journal(period, limit=500) if r.get("closed_at")]
        rows = sorted(rows, key=lambda r: str(first_present(r.get("closed_at"), r.get("created_at"))), reverse=True)
        total = sum(float_or_zero(r.get("profit")) for r in rows)
        wins = sum(1 for r in rows if float_or_zero(r.get("profit")) > 0)
        losses = sum(1 for r in rows if float_or_zero(r.get("profit")) < 0)
        closed = wins + losses
        winrate = round((wins / closed) * 100) if closed else 0
        body = []
        for row in rows[:10]:
            pnl = menu_number(row.get("profit"), 0, signed=True)
            risk = estimate_r_multiple(row)
            duration = menu_duration(row.get("opened_at"), row.get("closed_at"))
            parts = [menu_asset_from_trade(row).ljust(7), fmt_side(row.get("side")).ljust(4)]
            if pnl:
                parts.append(pnl.rjust(5))
            if risk:
                parts.append(risk.rjust(6))
            if duration:
                parts.append(duration)
            body.append(" ".join(parts))
        if not body:
            body.append("🟡 Нет сделок за выбранный период")
        body.extend(["", f"Итого:  {menu_number(total, 0, True) or '+0'}  |  {wins}W / {losses}L  |  {winrate}%"])
        return menu_message(f"📋 СДЕЛКИ  {period_title(period)}", body), menu_back_keyboard(f"trades_{period}", "menu_trades")
    except Exception:
        return menu_message("🔴 ОШИБКА СДЕЛОК", ["Не удалось получить журнал сделок."], True), menu_back_keyboard(f"trades_{period}", "menu_trades")


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


def render_stats_period() -> tuple[str, dict]:
    text = menu_message("📈 СТАТИСТИКА  ПЕРИОД", [], True)
    keyboard = inline_keyboard(
        [
            [("День", "stats_period_day"), ("Неделя", "stats_period_week"), ("Месяц", "stats_period_month")],
            [(" Меню", "menu_main")],
        ]
    )
    return text, keyboard


def render_stats_asset(period: str) -> tuple[str, dict]:
    text = menu_message("📈 СТАТИСТИКА  АКТИВ", [], True)
    keyboard = inline_keyboard(
        [
            [("Все", "stats_asset_all"), ("NAS100", "stats_asset_NAS100"), ("SP500", "stats_asset_SP500")],
            [("DJ30", "stats_asset_DJ30"), ("BTCUSD", "stats_asset_BTCUSD"), ("GER40", "stats_asset_GER40")],
            [(" Назад", "menu_stats")],
        ]
    )
    return text, keyboard


def render_stats_result(period: str, asset: str) -> tuple[str, dict]:
    try:
        store_period = {"day": "today", "week": "7d", "month": "30d"}.get(period, "today")
        selector = None if asset == "all" else asset
        summary = acct.performance_summary(store_period, selector)
        stats = summary.get("totals") or {}
        trades = int(stats.get("trades_count") or 0)
        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        win_pct = round((wins / trades) * 100) if trades else 0
        loss_pct = round((losses / trades) * 100) if trades else 0
        avg = (float_or_zero(stats.get("closed_pnl")) / trades) if trades else 0
        avg_r = average_r(menu_journal(store_period, selector))
        avg_dur = average_duration(menu_journal(store_period, selector))
        title_asset = "ВСЕ" if asset == "all" else asset
        body = [
            menu_line("🎯 Сделок:", str(trades)),
            menu_line("Побед:", f"{wins}  ({win_pct}%)"),
            menu_line("Убытков:", f"{losses}  ({loss_pct}%)"),
            "",
            menu_line("💰 P&L:", menu_number(stats.get("closed_pnl"), 0, True)),
            menu_line("🏆 Лучшая:", menu_number(stats.get("best_trade"), 0, True)),
            menu_line("📉 Худшая:", menu_number(stats.get("worst_trade"), 0, True)),
            menu_line("Средняя:", menu_number(avg, 0, True)),
            "",
            "📊 МЕТРИКИ",
            menu_line("Profit Factor:", fmt_pf(stats.get("profit_factor"))),
            menu_line("Avg R:", f"{avg_r:.2f}R" if avg_r is not None else None),
            menu_line("TP1 взят:", f"{round((int(stats.get('tp1_count') or 0) / trades) * 100)}%" if trades else None),
            menu_line("TP2 взят:", f"{round((int(stats.get('tp2_count') or 0) / trades) * 100)}%" if trades else None),
            menu_line("Avg время:", avg_dur),
        ]
        return menu_message(f"📈 {title_asset}  {period_title(period)}", body), menu_back_keyboard("stats_refresh", "stats_back_asset")
    except Exception:
        return menu_message("🔴 ОШИБКА СТАТИСТИКИ", ["Не удалось рассчитать статистику."], True), menu_back_keyboard("stats_refresh", "menu_stats")


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
    text = menu_message("🤖 УПРАВЛЕНИЕ БОТАМИ", [], True)
    keyboard = inline_keyboard(
        [
            [(" Включить бота", "action_enable")],
            [(" Остановить бота", "action_disable")],
            [(" Стоп все боты", "action_disable_all")],
            [(" Включить всех", "action_enable_all")],
            [("📋 Отчёт сейчас", "action_report")],
            [(" Меню", "menu_main")],
        ]
    )
    return text, keyboard


def render_action_select(action: str) -> tuple[str, dict]:
    title = "🤖 ВКЛЮЧИТЬ  ВЫБЕРИ БОТА" if action == "enable" else "🤖 ОСТАНОВИТЬ  ВЫБЕРИ БОТА"
    keyboard = inline_keyboard(
        [
            [("NAS100", "action_bot_NAS100"), ("SP500", "action_bot_SP500"), ("DJ30", "action_bot_DJ30")],
            [("BTCUSD", "action_bot_BTCUSD"), ("GER40", "action_bot_GER40"), ("Все", "action_bot_Все")],
            [(" Назад", "menu_actions")],
        ]
    )
    return menu_message(title, [], True), keyboard


def render_action_confirm(action: str, asset: str) -> tuple[str, dict]:
    verb = "Включить" if action in {"enable", "enable_all"} else "Остановить"
    target = "всех ботов" if asset == "Все" or action.endswith("_all") else f"бота: {asset}"
    text = menu_message("🟡 ПОДТВЕРЖДЕНИЕ", [f"{verb} {target}?"], True)
    keyboard = inline_keyboard([[(" Подтвердить", "action_confirm"), (" Отмена", "menu_actions")]])
    return text, keyboard


def execute_action(action: str, asset: str) -> tuple[str, dict]:
    try:
        enable = action in {"enable", "enable_all"}
        if asset == "Все" or action.endswith("_all"):
            controls = acct.set_all_native_bots_enabled(enable, "Telegram menu")
            target = "ВСЕ БОТЫ"
        else:
            control = acct.set_native_bot_enabled(asset, enable, "Telegram menu")
            controls = [control] if control else []
            target = asset
        status = "ВКЛЮЧЁН" if enable else "ОСТАНОВЛЕН"
        detail = "Новые входы разрешены" if enable else "Новые входы запрещены"
        body = [f"🤖 {target}  {status}", detail]
        if not controls:
            body = ["🔴 Бот не найден"]
        return menu_message("🟢 ВЫПОЛНЕНО", body, True), inline_keyboard([[(" Меню", "menu_main")]])
    except Exception:
        return menu_message("🔴 ОШИБКА", ["Действие не выполнено."], True), inline_keyboard([[(" Меню", "menu_main")]])


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
            menu_line("💰 P&L дня:", menu_number(total, 0, True)),
            menu_line("📊 Сделок:", str(trades)),
            menu_line("Побед:", f"{wins}  ({winrate}%)"),
            menu_line("Убытков:", str(losses)),
            "",
            "По активам:",
        ]
        for asset in MENU_ASSETS:
            if asset in by_asset:
                body.append(f"{asset:<8} {menu_number(by_asset[asset], 0, True)}")
        body.extend([
            "",
            f"🏆 Лучшая:   {menu_asset_from_trade(best) if best else ''} {menu_number(best.get('profit'), 0, True) if best else ''}".rstrip(),
            f"📉 Худшая:   {menu_asset_from_trade(worst) if worst else ''} {menu_number(worst.get('profit'), 0, True) if worst else ''}".rstrip(),
            menu_line("Avg R:", f"{average_r(rows):.2f}R" if average_r(rows) is not None else None),
        ])
        return menu_message("📅 ДНЕВНОЙ ОТЧЁТ", body), inline_keyboard([[(" Меню", "menu_main")]])
    except Exception:
        return menu_message("🔴 ОШИБКА ОТЧЁТА", ["Не удалось сформировать отчёт."], True), inline_keyboard([[(" Меню", "menu_main")]])


def render_market() -> tuple[str, dict]:
    body = []
    try:
        import yfinance as yf
        for asset, ticker in MARKET_SYMBOLS.items():
            try:
                hist = yf.Ticker(ticker).history(period="2d", interval="1d", timeout=6)
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
                    body.append(f"VIX:      {close:.1f}  {color} {level}")
                else:
                    color = "🟢" if change > 0.1 else "🔴" if change < -0.1 else "🟡"
                    body.append(f"{asset:<7} {close:>9,.0f}  {color} {change:+.2f}%")
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
            menu_line("💰 Лучший день:", f"{menu_number(best_day[1], 0, True)}  📅 {best_day[0]}" if best_day else None),
            menu_line("🎯 Лучшая сделка:", f"{menu_number(best_trade.get('profit'), 0, True)}  {menu_asset_from_trade(best_trade)}" if best_trade else None),
            menu_line("📈 Лучшая серия:", f"{streak} побед подряд"),
            menu_line("Макс R за сделку:", f"{max_r:.2f}R  {menu_asset_from_trade(max_r_row)}" if max_r_row else None),
            "",
            "📉 АНТИРЕКОРДЫ",
            menu_line("Худший день:", f"{menu_number(worst_day[1], 0, False)}  📅 {worst_day[0]}" if worst_day else None),
            menu_line("Худшая сделка:", f"{menu_number(worst_trade.get('profit'), 0, False)}  {menu_asset_from_trade(worst_trade)}" if worst_trade else None),
            menu_line("Макс просадка:", f"{max_drawdown_pct(rows):.1f}%"),
            "",
            "📊 ВСЕГО",
            menu_line("Сделок:", str(trades)),
            menu_line("P&L:", menu_number(total, 0, True)),
            menu_line("Винрейт:", f"{winrate}%"),
        ]
        return menu_message("🏆 РЕКОРДЫ", body), inline_keyboard([[(" Меню", "menu_main")]])
    except Exception:
        return menu_message("🔴 ОШИБКА РЕКОРДОВ", ["Не удалось получить рекорды."], True), inline_keyboard([[(" Меню", "menu_main")]])


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
        current_pnl = safe_float(pnl.get("total_pnl", pnl.get("net_pnl"))) or 0.0
        used = max(0.0, -current_pnl)
        used_pct = min(100, round((used / limit) * 100)) if limit else 0
        remaining = max(0.0, limit - used)
        status = "🟢 В НОРМЕ" if used_pct < 30 else "🟡 ВНИМАНИЕ" if used_pct <= 70 else "🔴 ЛИМИТ"
        risks = [estimate_position_risk(p) for p in positions]
        risks = [r for r in risks if r is not None]
        total_risk = sum(risks)
        max_risk = max(risks) if risks else None
        body = [
            menu_line("💰 Баланс начала дня:", menu_money(start.get("balance"), "$", 0)),
            menu_line("📉 Текущий P&L:", menu_money(current_pnl, "$", 0, signed=True)),
            "",
            " ЛИМИТЫ",
            menu_line("Дневной лимит убытка:", menu_money(limit, "$", 0)),
            menu_line("Использовано:", f"{menu_money(used, '$', 0)}  ({used_pct}%)"),
            menu_line("Осталось до лимита:", menu_money(remaining, "$", 0)),
            "",
            f"Статус: {status}",
            "",
            "📊 ОТКРЫТЫЕ ПОЗИЦИИ",
            menu_line("Позиций:", str(len(positions))),
            menu_line("Макс риск на позицию:", menu_money(max_risk, "$", 0)),
            menu_line("Суммарный риск:", menu_money(total_risk, "$", 0)),
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
        heartbeat = acct.last_mt5_heartbeat()
        server_online = "🟢 Online" if heartbeat else "🟡 Нет данных"
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
            cfg = "OK" if control.get("settings_summary") or control.get("last_heartbeat_at") else "FAIL"
            time_text = format_time(control.get("last_heartbeat_at")) if control.get("last_heartbeat_at") else ""
            body.append(f"{asset:<7} {'🟢 Активен' if active else '🔴 Выключен'}   cfg:{cfg:<4} {time_text}".rstrip())
        body.extend(["", f"Онлайн: {online}/5", f"Сервер: {server_online}", menu_line("Uptime:", format_heartbeat(heartbeat))])
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
        return menu_main_text()
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
            [{"text": "Включить бота"}, {"text": "Остановить бота"}],
            [{"text": "📊 Статистика"}, {"text": "📒 Журнал"}],
            [{"text": "📸 Последний скрин"}, {"text": "Настройки"}],
            [{"text": "Статус"}, {"text": "Сделки"}],
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
    mapping.update(
        {
            "Включить бота": "/enable_menu",
            "Остановить бота": "/disable_menu",
            "📊 Статистика": "/performance_menu",
            "📒 Журнал": "/journal",
            "📸 Последний скрин": "/screenshot_menu",
            "Настройки": "/bot_settings",
            "Статус": "/status",
            "Сделки": "/trades_today",
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
