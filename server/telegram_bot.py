import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

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


def notify_close_signal(payload: WebhookPayload) -> None:
    lines = [
        "Close signal received",
        f"symbol: {payload.mt5_symbol or payload.symbol}",
        f"side: {payload.side}",
        f"reason: {payload.reason}",
        f"parent_signal_id: {payload.parent_signal_id}",
    ]
    q.record_event(
        "close_signal_received",
        payload.signal_id,
        {
            "symbol": payload.mt5_symbol or payload.symbol,
            "side": payload.side,
            "reason": payload.reason,
            "parent_signal_id": payload.parent_signal_id,
        },
    )
    send_telegram_message("\n".join(lines))


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
    if not stripped.startswith("/"):
        return handle_natural_language_command(stripped, chat_id or config.TELEGRAM_ADMIN_CHAT_ID)

    parts = stripped.split()
    command = parts[0].lower()

    if command == "/status":
        counts = q.command_counts()
        last_report = q.last_execution_report()
        return "\n".join(
            [
                "Server status",
                "server: running",
                f"trading: {'enabled' if get_setting('trading_enabled', config.TRADING_ENABLED) else 'disabled'}",
                f"dry_run: {get_setting('dry_run', True)}",
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

    if command == "/news":
        return attach_ai_risk_action_approval(get_market_news_today(), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/calendar":
        return attach_ai_risk_action_approval(get_economic_calendar_today(), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/market_today":
        return attach_ai_risk_action_approval(get_market_today_summary(), chat_id or config.TELEGRAM_ADMIN_CHAT_ID, stripped)

    if command == "/ask":
        question = stripped[len(parts[0]) :].strip()
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
                "/status - server and queue status",
                "/last_trade - latest execution report",
                "/today - today's signal summary",
                "/news - today's market news",
                "/calendar - today's economic calendar",
                "/market_today - market risk overview",
                "/ask <question> - ask AI research assistant",
                "/settings - bot settings",
                "/risk - risk settings",
                "/approvals - pending approvals",
                "/confirm <approval_id> - apply pending change",
                "/reject <approval_id> - reject pending change",
                "/pause - request trading pause",
                "/resume - request trading resume",
                "/dryrun_on - request dry run on",
                "/dryrun_off - blocked in demo-first mode",
                "/help - command list",
            ]
        )

    return "Unknown command. Use /help."


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
        return attach_ai_risk_action_approval(get_market_news_today(), chat_id, text)
    if "календар" in normalized and any(word in normalized for word in ("сегодня", "рын", "эконом")):
        return attach_ai_risk_action_approval(get_economic_calendar_today(), chat_id, text)
    if "что сегодня важно" in normalized or "риск по рынку" in normalized:
        return attach_ai_risk_action_approval(get_market_today_summary(), chat_id, text)

    asset = detect_asset_query(normalized)
    if asset and any(marker in normalized for marker in ("влияет", "падает", "растет", "движ", "почему", "сегодня")):
        return attach_ai_risk_action_approval(get_asset_impact_summary(asset), chat_id, text)
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
