import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from . import account_store as acct
from . import queue as q
from .ai_command_parser import SYMBOLS, parse_natural_language_command
from .ai_web_research import (
    answer_with_web_search,
    get_asset_impact_summary,
    get_economic_calendar_today,
    get_market_news_today,
    get_market_today_summary,
)
from .models import WebhookPayload
from .settings_store import (
    approve_pending_approval,
    create_pending_approval,
    get_setting,
    list_pending_approvals,
    list_settings,
    reject_pending_approval,
)


NOTIFY_EXECUTION_STATUSES = {
    "open_failed",
    "opened",
    "tp1_closed",
    "tp2_closed",
    "tp3_closed",
    "be_moved",
    "position_closed",
}

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

_EVENT_ICONS = {
    "webhook_signal_received": "📥",
    "command_queued": "📋",
    "mt5_command_sent": "📤",
    "ack_received": "✅",
    "execution_report_received": "📊",
    "rejected_signal": "❌",
    "close_signal_received": "🔻",
}


def send_telegram_message(text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ADMIN_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "reply_markup": json.dumps(dashboard_keyboard(), ensure_ascii=False),
        }
    ).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            return True
    except Exception:
        return False


def notify_event(event_type: str, signal_id: Optional[str] = None, details: Optional[str] = None) -> None:
    icon = _EVENT_ICONS.get(event_type, "🔔")
    title = event_type.replace("_", " ").upper()
    parts = [f"{icon} {title}"]
    if signal_id:
        parts.append(f"Signal: {signal_id}")
    if details:
        parts.append(details)
    q.record_event(event_type, signal_id, {"details": details})
    send_telegram_message("\n".join(parts))


def notify_close_signal(payload: WebhookPayload) -> None:
    symbol = payload.mt5_symbol or payload.symbol
    lines = [
        "🔻 CLOSE SIGNAL",
        f"Asset: {symbol}",
        f"Side: {(payload.side or 'n/a').upper()}",
        f"Reason: {payload.reason or 'n/a'}",
        f"Signal: {payload.signal_id}",
    ]
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
    send_telegram_message("\n".join(lines))


def notify_execution(status: str, report) -> None:
    payload = q.get_command_payload(report.signal_id)
    text = _format_execution_notification(status, report, payload)
    q.record_event(status, report.signal_id, {"ticket": report.ticket, "message": report.message})
    send_telegram_message(text)


def _format_execution_notification(status: str, report, payload: Optional[dict]) -> str:
    p = payload or {}
    symbol = p.get("mt5_symbol") or p.get("symbol") or "n/a"
    side = (p.get("side") or "n/a").upper()
    lot = p.get("lot")
    sl = p.get("sl")
    tp1 = p.get("tp1")
    tp2 = p.get("tp2")
    tp3 = p.get("tp3")

    if status == "opened":
        lines = [
            "🚀 TRADE OPENED",
            f"Asset: {symbol}",
            f"Side: {side}",
            f"Lot: {fmt_money(lot) if lot is not None else 'n/a'}",
            f"Entry: {fmt_price(report.executed_price)}",
            f"SL: {fmt_price(sl)}",
        ]
        if tp1 is not None:
            lines.append(f"TP1: {fmt_price(tp1)}")
        if tp2 is not None:
            lines.append(f"TP2: {fmt_price(tp2)}")
        if tp3 is not None:
            lines.append(f"TP3: {fmt_price(tp3)}")
        lines.append(f"Signal: {report.signal_id}")
        return "\n".join(lines)

    if status in ("position_closed", "tp1_closed", "tp2_closed", "tp3_closed"):
        reason_labels = {
            "tp1_closed": "TP1 hit",
            "tp2_closed": "TP2 hit",
            "tp3_closed": "TP3 hit",
            "position_closed": "Position closed",
        }
        lines = [
            "🏁 TRADE CLOSED",
            f"Asset: {symbol}",
            f"Side: {side}",
            f"Lot: {fmt_money(lot) if lot is not None else 'n/a'}",
            f"Entry: {fmt_price(p.get('entry'))}",
            f"Exit: {fmt_price(report.executed_price)}",
            f"Reason: {reason_labels.get(status, status)}",
        ]
        if report.ticket:
            lines.append(f"Ticket: {report.ticket}")
        if report.message:
            lines.append(f"Note: {report.message}")
        return "\n".join(lines)

    if status == "open_failed":
        return "\n".join([
            "🚨 EXECUTION ERROR",
            f"Signal: {report.signal_id}",
            f"Asset: {symbol}",
            f"Error: {report.message or 'n/a'}",
            "Action required: check MT5 manually",
        ])

    if status == "be_moved":
        return f"📍 BE MOVED\nAsset: {symbol}\nTicket: {report.ticket or 'n/a'}"

    return f"MT5: {status}\n{report.signal_id}\n{report.message or ''}"


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


def handle_command(text: str, chat_id: Optional[str] = None) -> str:
    stripped = text.strip()
    if not stripped:
        return "Empty command."
    stripped = normalize_dashboard_button(stripped)
    if not stripped.startswith("/"):
        return handle_natural_language_command(stripped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID)

    parts = stripped.split()
    command = parts[0].lower()

    if command == "/status":
        counts = q.command_counts()
        account = acct.latest_account_snapshot()
        positions = acct.current_positions()
        pnl = acct.pnl_today()
        heartbeat = acct.last_mt5_heartbeat()
        trading_on = get_setting("trading_enabled", config.TRADING_ENABLED)
        dry_run = get_setting("dry_run", True)
        mt5_link = "ACTIVE" if heartbeat else "OFFLINE"
        trading_status = "ENABLED" if trading_on else "PAUSED"
        dry_run_status = "ON" if dry_run else "OFF"
        currency = account.get("currency") or "" if account else ""

        lines = [
            "⚡ SYSTEM CORE",
            "",
            f"🟢 Server: ONLINE",
            f"{'🟢' if heartbeat else '🔴'} MT5 Link: {mt5_link}",
            f"{'🟢' if trading_on else '🟡'} Trading: {trading_status}",
            f"🟡 DryRun: {dry_run_status}",
            "",
            "💰 ACCOUNT",
            f"Balance: {fmt_money(account.get('balance') if account else None)} {currency}".strip(),
            f"Equity: {fmt_money(account.get('equity') if account else None)} {currency}".strip(),
            f"Today PnL: {fmt_money(pnl['net_pnl'])} {currency}".strip(),
            "",
            "📡 EXECUTION",
            f"Open positions: {len(positions)}",
            f"Queued commands: {counts.get('queued', 0)}",
            f"Last MT5 heartbeat: {heartbeat or 'n/a'}",
        ]
        if account and is_real_trade_mode(account.get("trade_mode")):
            lines.append("")
            lines.append("⚠️ REAL ACCOUNT DETECTED")
        return "\n".join(lines)

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

    if command == "/account":
        return format_account()

    if command in ("/balance", "/equity"):
        account = acct.latest_account_snapshot()
        if not account:
            return "No account snapshot yet."
        key = command.replace("/", "")
        return f"{key}: {fmt_money(account.get(key))} {account.get('currency') or ''}".strip()

    if command == "/positions":
        return format_positions()

    if command == "/trades":
        return format_trades_today()

    if command in ("/history_today", "/pnl_today"):
        return format_history_today()

    if command == "/news":
        raw = get_market_news_today()
        wrapped = _wrap_market_header("📰 MARKET INTEL", raw)
        return attach_ai_risk_action_approval(wrapped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/calendar":
        raw = get_economic_calendar_today()
        wrapped = _wrap_market_header("📅 ECONOMIC CALENDAR", raw)
        return attach_ai_risk_action_approval(wrapped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/market_today":
        raw = get_market_today_summary()
        wrapped = _wrap_market_header("📰 MARKET INTEL", raw)
        return attach_ai_risk_action_approval(wrapped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/ask":
        question = stripped[len(parts[0]):].strip()
        if not question:
            return "Usage: /ask <question>"
        return attach_ai_risk_action_approval(
            answer_with_web_search(question, format_risk()),
            chat_id or config.TELEGRAM_ADMIN_CHAT_ID,
            stripped,
        )

    if command == "/settings":
        return format_settings()

    if command == "/risk":
        return format_risk()

    if command == "/approvals":
        return format_approvals(chat_id or config.TELEGRAM_ADMIN_CHAT_ID)

    if command == "/confirm":
        if len(parts) < 2:
            return "Usage: /confirm <approval_id>"
        ok, message, approval = approve_pending_approval(parts[1], chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
        if ok and approval:
            return f"Применено\nApproval ID: {approval['approval_id']}"
        return message

    if command == "/reject":
        if len(parts) < 2:
            return "Usage: /reject <approval_id>"
        ok, message, approval = reject_pending_approval(parts[1], chat_id or config.TELEGRAM_ADMIN_CHAT_ID)
        if ok and approval:
            return f"Отклонено\nApproval ID: {approval['approval_id']}"
        return message

    if command == "/pause":
        parsed = {
            "intent": "pause_trading",
            "setting_key": "trading_enabled",
            "operation": "disable",
            "value": False,
            "symbol": None,
        }
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, parsed)

    if command == "/resume":
        parsed = {
            "intent": "resume_trading",
            "setting_key": "trading_enabled",
            "operation": "enable",
            "value": True,
            "symbol": None,
        }
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, parsed)

    if command == "/dryrun_on":
        parsed = {
            "intent": "change_setting",
            "setting_key": "dry_run",
            "operation": "enable",
            "value": True,
            "symbol": None,
        }
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, parsed)

    if command == "/dryrun_off":
        parsed = {
            "intent": "change_setting",
            "setting_key": "dry_run",
            "operation": "disable",
            "value": False,
            "symbol": None,
        }
        return create_change_approval(chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped, parsed)

    if command == "/help":
        return "\n".join(
            [
                "Commands",
                "/status - system core overview",
                "/last_trade - latest execution report",
                "/today - today's signal summary",
                "/account - MT5 account matrix",
                "/balance - account balance",
                "/equity - account equity",
                "/positions - open positions",
                "/trades - today's closed trades",
                "/history_today - daily performance summary",
                "/pnl_today - today's PnL summary",
                "/news - market intel (news)",
                "/calendar - economic calendar",
                "/market_today - market risk overview",
                "/ask <question> - AI research assistant",
                "/settings - bot settings",
                "/risk - risk controls",
                "/approvals - pending approvals",
                "/confirm <id> - apply pending change",
                "/reject <id> - reject pending change",
                "/pause - request trading pause",
                "/resume - request trading resume",
                "/dryrun_on - request dry run on",
                "/dryrun_off - blocked in demo-first mode",
                "/help - command list",
            ]
        )

    return "Unknown command. Use /help."


def dashboard_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "📊 Core Status"}, {"text": "📈 Trade Center"}],
            [{"text": "📰 Market Intel"}, {"text": "⚙️ Control Panel"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def normalize_dashboard_button(text: str) -> str:
    mapping = {
        # new buttons
        "📊 Core Status": "/status",
        "📈 Trade Center": "/trades",
        "📰 Market Intel": "/market_today",
        "⚙️ Control Panel": "/settings",
        # legacy buttons (keep working)
        "Статус": "/status",
        "Сделки": "/trades",
        "Новости": "/market_today",
        "⚙️ Управление": "/settings",
    }
    return mapping.get(text, text)


def _wrap_market_header(header: str, body: str) -> str:
    lines = body.strip().splitlines()
    # strip AI-generated title line if it matches a known heading pattern
    if lines and not lines[0].startswith(("Вывод", "События", "Риск", "Sources", "Market")):
        pass
    else:
        # body already has structure, keep as-is
        pass
    return f"{header}\n\n{body.strip()}"


def handle_natural_language_command(text: str, chat_id: str) -> str:
    symbol_pause = parse_symbol_pause_request(text)
    if symbol_pause:
        return create_change_approval(chat_id, text, symbol_pause)

    market_response = handle_market_language_query(text, chat_id)
    if market_response:
        return market_response

    parsed = parse_natural_language_command(text)
    if parsed.intent == "show_settings":
        return format_settings()
    if parsed.intent == "show_status":
        return format_risk()
    if parsed.intent == "show_last_trade":
        return format_execution_report(q.last_execution_report())
    if parsed.intent == "unknown" or parsed.confidence < 0.65:
        return "Не удалось надежно распознать команду. Используй /help или сформулируй точнее."

    parsed_action = parsed.model_dump()
    if parsed.intent == "pause_trading":
        parsed_action.update({"setting_key": "trading_enabled", "operation": "disable", "value": False})
    if parsed.intent == "resume_trading":
        parsed_action.update({"setting_key": "trading_enabled", "operation": "enable", "value": True})
    return create_change_approval(chat_id, text, parsed_action)


def handle_market_language_query(text: str, chat_id: str) -> Optional[str]:
    normalized = text.lower()
    if any(phrase in normalized for phrase in ("какие новости сегодня", "новости сегодня", "что сегодня важно по рынку")):
        raw = get_market_news_today()
        return attach_ai_risk_action_approval(_wrap_market_header("📰 MARKET INTEL", raw), chat_id, text)
    if "календар" in normalized and any(word in normalized for word in ("сегодня", "рын", "эконом")):
        raw = get_economic_calendar_today()
        return attach_ai_risk_action_approval(_wrap_market_header("📅 ECONOMIC CALENDAR", raw), chat_id, text)
    if "что сегодня важно" in normalized or "риск по рынку" in normalized:
        raw = get_market_today_summary()
        return attach_ai_risk_action_approval(_wrap_market_header("📰 MARKET INTEL", raw), chat_id, text)

    asset = detect_asset_query(normalized)
    if asset and any(marker in normalized for marker in ("влияет", "падает", "растет", "движ", "почему", "сегодня")):
        raw = get_asset_impact_summary(asset)
        return attach_ai_risk_action_approval(_wrap_market_header("📰 MARKET INTEL", raw), chat_id, text)
    if any(marker in normalized for marker in ("почему nas100", "почему us500", "почему sp500", "почему dj30", "почему xau", "почему btc")):
        return attach_ai_risk_action_approval(answer_with_web_search(text, format_risk()), chat_id, text)
    return None


def attach_ai_risk_action_approval(response: str, chat_id: str, command_text: str) -> str:
    parsed_action = parse_symbol_pause_request(response)
    if not parsed_action:
        return response
    parsed_action["reason"] = "high impact news"
    approval_text = create_change_approval(chat_id, command_text, parsed_action)
    return response.rstrip() + "\n\nPending approval created from AI risk suggestion:\n" + approval_text


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
        "btc": "BTCUSD",
        "btcusd": "BTCUSD",
        "биткоин": "BTCUSD",
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
        return f"Risk validation rejected command:\n{validation_error}"

    parsed_action["setting_key"] = setting_key
    parsed_action["new_value"] = new_value
    approval = create_pending_approval(chat_id, command_text, parsed_action, old_value, new_value)
    return "\n".join(
        [
            "Команда распознана:",
            f"Параметр: {setting_key}",
            f"Старое значение: {old_value}",
            f"Новое значение: {new_value}",
            f"Approval ID: {approval['approval_id']}",
            "",
            "Для применения напиши:",
            f"/confirm {approval['approval_id']}",
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
        return "Missing setting_key."
    if setting_key not in KNOWN_SETTING_KEYS:
        return f"Unknown setting: {setting_key}."
    allowed = [
        item.strip()
        for item in str(get_setting("allowed_symbols", "XAUUSD,NAS100,DJ30,US500,BTCUSD")).split(",")
        if item.strip()
    ]
    if symbol and symbol not in allowed:
        return f"Unknown or disallowed symbol: {symbol}."
    if setting_key.startswith("symbol_lot_multiplier_"):
        setting_symbol = setting_key.replace("symbol_lot_multiplier_", "")
        if setting_symbol not in SYMBOLS or setting_symbol not in allowed:
            return f"Unknown or disallowed symbol: {setting_symbol}."
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "Lot multiplier must be numeric."
        if numeric_value <= 0:
            return "Lot multiplier must be greater than 0."
        if numeric_value > 3.0:
            return "Lot multiplier cannot exceed 3.0."
    if setting_key.startswith("symbol_paused_until_"):
        setting_symbol = setting_key.replace("symbol_paused_until_", "")
        if setting_symbol not in SYMBOLS or setting_symbol not in allowed:
            return f"Unknown or disallowed symbol: {setting_symbol}."
        if new_value:
            try:
                datetime.fromisoformat(str(new_value))
            except ValueError:
                return "symbol pause expiry must be an ISO datetime."
    if setting_key == "global_lot_multiplier":
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "global_lot_multiplier must be numeric."
        if numeric_value <= 0:
            return "global_lot_multiplier must be greater than 0."
        if numeric_value > 3.0:
            return "global_lot_multiplier cannot exceed 3.0."
    if setting_key == "max_lot":
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return "max_lot must be numeric."
        if numeric_value <= 0:
            return "max_lot must be greater than 0."
        if numeric_value > 1.0:
            return "max_lot cannot exceed 1.0 in demo-first mode."
    if setting_key == "dry_run" and new_value is False:
        return "dry_run=false is blocked in demo-first mode. Live trading is not enabled from Telegram."
    if setting_key in ("dry_run", "use_server_lot") and not isinstance(new_value, bool):
        return f"{setting_key} must be boolean."
    if setting_key == "trading_enabled" and not isinstance(new_value, bool):
        return "trading_enabled must be boolean."
    if setting_key in ("max_daily_loss", "max_trades_per_day"):
        try:
            numeric_value = float(new_value)
        except (TypeError, ValueError):
            return f"{setting_key} must be numeric."
        if numeric_value < 0:
            return f"{setting_key} cannot be negative."
    if setting_key == "allowed_symbols":
        requested = [item.strip() for item in str(new_value).split(",") if item.strip()]
        unknown = [item for item in requested if item not in SYMBOLS]
        if unknown:
            return f"Unknown symbols are not allowed: {', '.join(unknown)}."
    return None


def format_account() -> str:
    account = acct.latest_account_snapshot()
    if not account:
        return "No account snapshot yet."
    currency = account.get("currency") or ""
    mode = format_trade_mode(account.get("trade_mode")).upper()
    lines = [
        "💰 ACCOUNT MATRIX",
        "",
        f"Login: {mask_account_login(account.get('account_login'))}",
        f"Server: {account.get('account_server') or 'n/a'}",
        f"Mode: {mode}",
        f"Balance: {fmt_money(account.get('balance'))} {currency}".strip(),
        f"Equity: {fmt_money(account.get('equity'))} {currency}".strip(),
        f"Margin: {fmt_money(account.get('margin'))} {currency}".strip(),
        f"Free margin: {fmt_money(account.get('free_margin'))} {currency}".strip(),
        f"Margin level: {fmt_money(account.get('margin_level'))}%",
    ]
    if is_real_trade_mode(account.get("trade_mode")):
        lines.append("")
        lines.append("⚠️ REAL ACCOUNT DETECTED")
    return "\n".join(lines)


def format_positions() -> str:
    positions = acct.current_positions()
    if not positions:
        return "📭 NO OPEN POSITIONS"
    lines = ["📈 OPEN POSITIONS", ""]
    for i, position in enumerate(positions[:10], 1):
        symbol = position.get("symbol") or "n/a"
        side = (position.get("side") or "n/a").upper()
        lines.append(f"{i}. {symbol} {side}")
        lines.append(f"Lot: {fmt_money(position.get('lot'))}")
        lines.append(f"Entry: {fmt_price(position.get('entry_price'))}")
        lines.append(f"Current: {fmt_price(position.get('current_price'))}")
        lines.append(f"SL: {fmt_price(position.get('sl'))}")
        lines.append(f"TP: {fmt_price(position.get('tp'))}")
        lines.append(f"Floating PnL: {fmt_money(position.get('profit'))}")
        lines.append(f"Ticket: {position.get('ticket') or 'n/a'}")
        if i < len(positions[:10]):
            lines.append("")
    if len(positions) > 10:
        lines.append(f"...and {len(positions) - 10} more")
    return "\n".join(lines)


def format_trades_today() -> str:
    trades = acct.trades_today()
    if not trades:
        return "📭 NO TRADES TODAY"
    lines = ["🏁 TODAY TRADES", ""]
    for i, trade in enumerate(trades[:10], 1):
        symbol = trade.get("symbol") or "n/a"
        side = (trade.get("side") or "n/a").upper()
        lines.append(f"{i}. {symbol} {side}")
        lines.append(f"Entry: {fmt_price(trade.get('entry_price'))}")
        lines.append(f"Exit: {fmt_price(trade.get('exit_price'))}")
        lines.append(f"Lot: {fmt_money(trade.get('lot'))}")
        lines.append(f"Net PnL: {fmt_money(trade.get('net_profit'))}")
        lines.append(f"Reason: {trade.get('reason') or 'n/a'}")
        if i < len(trades[:10]):
            lines.append("")
    if len(trades) > 10:
        lines.append(f"...and {len(trades) - 10} more")
    return "\n".join(lines)


def format_history_today() -> str:
    summary = acct.pnl_today()
    trades = summary["trades_count"]
    wins = summary["wins"]
    losses = summary["losses"]
    winrate = f"{round(wins / trades * 100)}%" if trades > 0 else "n/a"
    return "\n".join(
        [
            "📊 DAILY PERFORMANCE",
            "",
            f"Trades: {trades}",
            f"Wins: {wins}",
            f"Losses: {losses}",
            f"Winrate: {winrate}",
            f"Net PnL: {fmt_money(summary['net_pnl'])}",
            f"Best trade: {fmt_money(summary['best_trade'])}",
            f"Worst trade: {fmt_money(summary['worst_trade'])}",
        ]
    )


def mask_account_login(login) -> str:
    if not login:
        return "n/a"
    text = str(login)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def format_trade_mode(trade_mode) -> str:
    if trade_mode is None:
        return "n/a"
    normalized = str(trade_mode).lower()
    if normalized in ("0", "demo"):
        return "demo"
    if normalized in ("1", "real", "live"):
        return "real"
    return str(trade_mode)


def is_real_trade_mode(trade_mode) -> bool:
    return format_trade_mode(trade_mode) == "real"


def fmt_money(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_price(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.5f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def format_settings() -> str:
    settings = list_settings()
    lines = ["Settings"]
    for key, data in settings.items():
        lines.append(f"{key}: {data['value']}")
    return "\n".join(lines)


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
    lines = ["Risk"]
    for key in keys:
        lines.append(f"{key}: {get_setting(key)}")
    return "\n".join(lines)


def format_approvals(chat_id: str) -> str:
    approvals = list_pending_approvals(chat_id)
    if not approvals:
        return "No pending approvals."
    lines = ["Pending approvals"]
    for approval in approvals:
        parsed = json.loads(approval["parsed_action"])
        lines.extend(
            [
                f"ID: {approval['approval_id']}",
                f"parameter: {parsed.get('setting_key')}",
                f"old: {approval['old_value']}",
                f"new: {approval['new_value']}",
                f"expires_at: {approval['expires_at']}",
            ]
        )
    return "\n".join(lines)


def parse_telegram_update(update: dict) -> tuple[Optional[str], Optional[str]]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return (str(chat_id) if chat_id is not None else None, text)
