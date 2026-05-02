import json
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
from .models import WebhookPayload
from .settings_store import (
    approve_pending_approval,
    create_pending_approval,
    get_setting,
    list_pending_approvals,
    reject_pending_approval,
)


BERLIN_TZ = ZoneInfo("Europe/Berlin")
DIVIDER = "━━━━━━━━━━━━━━━━━━"
THIN_DIVIDER = "──────────────────"
ASSETS_LINE = "XAUUSD · NAS100 · DJ30 · US500 · BTCUSD"

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


def notify_event(event_type: str, signal_id: Optional[str] = None, details: Optional[str] = None) -> None:
    q.record_event(event_type, signal_id, {"details": details})
    send_telegram_message(format_notification(event_type, signal_id, details))


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
    send_telegram_message(
        "\n".join(
            [
                "🔻 СИГНАЛ НА ЗАКРЫТИЕ",
                fmt_divider(),
                "",
                f"Актив: {symbol or 'нет данных'}",
                f"Сторона: {fmt_side(payload.side)}",
                f"Причина: {payload.reason or 'close signal'}",
                f"Parent: {payload.parent_signal_id or 'нет данных'}",
                f"Signal: {short_text(payload.signal_id, 42)}",
                "",
                fmt_divider(),
            ]
        )
    )


def notify_execution(status: str, report) -> None:
    payload = q.get_command_payload(report.signal_id)
    q.record_event(status, report.signal_id, {"ticket": report.ticket, "message": report.message})
    send_telegram_message(format_execution_notification(status, report, payload))


def format_execution_notification(status: str, report, payload: Optional[dict]) -> str:
    payload = payload or {}
    symbol = payload.get("mt5_symbol") or payload.get("symbol") or "нет данных"
    side = payload.get("side") or "нет данных"
    lot = payload.get("lot")
    if status == "opened":
        return "\n".join(
            [
                "🟢 СДЕЛКА ОТКРЫТА",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                f"Сторона: {fmt_side(side)}",
                f"Лот: {fmt_price(lot)}",
                f"Вход: {fmt_price(report.executed_price)}",
                f"SL: {fmt_price(payload.get('sl'))}",
                "",
                fmt_section("Цели"),
                f"TP1: {fmt_price(payload.get('tp1'))}",
                f"TP2: {fmt_price(payload.get('tp2'))}",
                f"TP3: {fmt_price(payload.get('tp3'))}",
                "",
                f"Signal: {short_text(report.signal_id, 54)}",
                "",
                fmt_divider(),
            ]
        )
    if status == "position_closed":
        return "\n".join(
            [
                "🔴 СДЕЛКА ЗАКРЫТА",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                f"Сторона: {fmt_side(side)}",
                f"Лот: {fmt_price(lot)}",
                "",
                f"Вход: {fmt_price(payload.get('entry'))}",
                f"Выход: {fmt_price(report.executed_price)}",
                f"Net PnL: {extract_pnl(report.message)}",
                "",
                f"Причина: {payload.get('reason') or 'close signal'}",
                f"Ticket: {report.ticket or 'нет данных'}",
                "",
                fmt_divider(),
            ]
        )
    if status in ("tp1_closed", "tp2_closed", "tp3_closed"):
        return "\n".join(
            [
                "🎯 TAKE PROFIT",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                f"Цель: {status[:3].upper()}",
                f"Закрыто: {extract_closed_part(report.message)}",
                f"PnL: {extract_pnl(report.message)}",
            ]
        )
    if status == "be_moved":
        return "\n".join(
            [
                "🛡 БЕЗУБЫТОК",
                fmt_divider(),
                "",
                f"Актив: {symbol}",
                "SL перенесён в Entry.",
                f"Цена BE: {fmt_price(payload.get('entry') or report.executed_price)}",
            ]
        )
    if status == "open_failed":
        return format_execution_error(report.signal_id, symbol, report.message or "нет данных")
    return format_notification(status, report.signal_id, report.message)


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
    if command in ("/history_today", "/pnl_today"):
        return format_history_today()
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
            [{"text": "Новости"}, {"text": "⚙️ Управление"}],
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
        "📊 Core Status": "/status",
        "📈 Trade Center": "/trades",
        "📰 Market Intel": "/market_today",
        "⚙️ Control Panel": "/settings",
        "Статус": "/status",
        "Сделки": "/trades",
        "Новости": "/market_today",
        "Управление": "/settings",
        "⚙️ Управление": "/settings",
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
    if setting_key == "dry_run" and new_value is False:
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
            f"Активы: {ASSETS_LINE}",
        ]
    )


def format_status() -> str:
    counts = q.command_counts()
    account = acct.latest_account_snapshot()
    positions = acct.current_positions()
    pnl = acct.pnl_today()
    currency = account.get("currency") if account else "USD"
    heartbeat = acct.last_mt5_heartbeat()
    mt5_active = heartbeat is not None
    trading_enabled = bool(get_setting("trading_enabled", config.TRADING_ENABLED))
    dry_run = bool(get_setting("dry_run", True))
    lines = [
        "⚡ AI TRADING CORE",
        fmt_divider(),
        "",
        fmt_section("СИСТЕМА"),
        f"Сервер:  {fmt_status_dot(True)} ONLINE",
        f"MT5:  {fmt_status_dot(mt5_active)} {'ACTIVE' if mt5_active else 'OFFLINE'}",
        f"Торговля:  {'ENABLED' if trading_enabled else '⏸ PAUSED'}",
        f"DryRun:  {'ON' if dry_run else '⚪ OFF'}",
        "",
        fmt_section("СЧЁТ"),
        f"Баланс: {fmt_money(account.get('balance') if account else None, currency) if account else 'нет данных'}",
        f"Equity: {fmt_money(account.get('equity') if account else None, currency) if account else 'нет данных'}",
        f"PnL сегодня: {fmt_pnl(pnl.get('net_pnl'), currency)}",
        "",
        fmt_section("ИСПОЛНЕНИЕ"),
        f"Открытых позиций: {len(positions)}",
        f"Команд в очереди: {counts.get('queued', 0)}",
        f"MT5 heartbeat: {format_heartbeat(heartbeat)}",
        "",
        fmt_section("АКТИВЫ"),
        ASSETS_LINE,
    ]
    if account and is_real_trade_mode(account.get("trade_mode")):
        lines.extend(["", "⚠️ REAL ACCOUNT DETECTED", "Проверить риск перед торговлей."])
    lines.extend(["", fmt_divider(), f"Обновлено: {berlin_now()} Berlin"])
    return "\n".join(lines)


def format_account() -> str:
    account = acct.latest_account_snapshot()
    if not account:
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
    currency = account.get("currency") or "USD"
    lines = [
        "💠 ACCOUNT MATRIX",
        fmt_divider(),
        "",
        f"Логин: {mask_login(account.get('account_login'))}",
        f"Сервер: {account.get('account_server') or 'нет данных'}",
        f"Режим: {format_trade_mode(account.get('trade_mode')).upper()}",
        f"Валюта: {currency}",
        "",
        f"Баланс: {fmt_money(account.get('balance'), '')}",
        f"Equity: {fmt_money(account.get('equity'), '')}",
        f"Маржа: {fmt_money(account.get('margin'), '')}",
        f"Свободно: {fmt_money(account.get('free_margin'), '')}",
        f"Margin Level: {fmt_percent(account.get('margin_level'))}",
    ]
    if is_real_trade_mode(account.get("trade_mode")):
        lines.extend(["", "⚠️ REAL ACCOUNT DETECTED", "Проверить риск перед торговлей."])
    lines.extend(["", fmt_divider()])
    return "\n".join(lines)


def format_account_short(key: str) -> str:
    account = acct.latest_account_snapshot()
    if not account:
        return format_account()
    currency = account.get("currency") or "USD"
    label = "Баланс" if key == "balance" else "Equity"
    return "\n".join(["💠 ACCOUNT MATRIX", fmt_divider(), f"{label}: {fmt_money(account.get(key), currency)}"])


def format_positions() -> str:
    positions = acct.current_positions()
    if not positions:
        return "\n".join(["📭 ОТКРЫТЫХ ПОЗИЦИЙ НЕТ", fmt_divider(), "", "Система подключена.", "Новых активных позиций нет."])
    account = acct.latest_account_snapshot() or {}
    currency = account.get("currency") or "USD"
    total = 0.0
    lines = ["📈 ОТКРЫТЫЕ ПОЗИЦИИ", fmt_divider(), ""]
    for index, position in enumerate(positions[:10], start=1):
        total += float_or_zero(position.get("profit"))
        if index > 1:
            lines.extend(["", THIN_DIVIDER, ""])
        lines.extend(
            [
                f"{index}. {position.get('symbol') or 'нет данных'}  {fmt_side(position.get('side'))}",
                f"Лот: {fmt_price(position.get('lot'))}",
                f"Вход: {fmt_price(position.get('entry_price'))}",
                f"Цена: {fmt_price(position.get('current_price'))}",
                f"SL: {fmt_price(position.get('sl'))}",
                f"TP: {fmt_price(position.get('tp'))}",
                f"PnL: {fmt_pnl(position.get('profit'), currency)}",
                f"Ticket: {position.get('ticket') or 'нет данных'}",
            ]
        )
    if len(positions) > 10:
        lines.append(f"Ещё позиций: {len(positions) - 10}")
    lines.extend(["", fmt_divider(), f"Floating PnL: {fmt_pnl(total, currency)}"])
    return "\n".join(lines)


def format_trades_today() -> str:
    trades = acct.trades_today()
    if not trades:
        return "\n".join(["📭 СЕГОДНЯ СДЕЛОК НЕТ", fmt_divider()])
    account = acct.latest_account_snapshot() or {}
    currency = account.get("currency") or "USD"
    total = 0.0
    lines = ["💼 СДЕЛКИ СЕГОДНЯ", fmt_divider(), ""]
    for index, trade in enumerate(trades[:10], start=1):
        total += float_or_zero(trade.get("net_profit"))
        if index > 1:
            lines.extend(["", THIN_DIVIDER, ""])
        lines.extend(
            [
                f"{index}. {trade.get('symbol') or 'нет данных'} {fmt_side(trade.get('side'))}",
                f"Лот: {fmt_price(trade.get('lot'))}",
                f"Вход: {fmt_price(trade.get('entry_price'))}",
                f"Выход: {fmt_price(trade.get('exit_price'))}",
                f"Net PnL: {fmt_pnl(trade.get('net_profit'), currency)}",
                f"Причина: {trade.get('reason') or 'close signal'}",
                f"Время: {format_time(trade.get('closed_at') or trade.get('created_at'))}",
            ]
        )
    if len(trades) > 10:
        lines.append(f"Ещё сделок: {len(trades) - 10}")
    lines.extend(["", fmt_divider(), f"Итого: {fmt_pnl(total, currency)}"])
    return "\n".join(lines)


def format_history_today() -> str:
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
    currency = account.get("currency") or "USD"
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
    return "\n".join(["⚠️ ОШИБКА ИСПОЛНЕНИЯ", fmt_divider(), "", f"Signal: {short_text(signal_id, 54)}", f"Актив: {symbol}", f"Ошибка: {short_text(error, 180)}", "", "Проверить:", "1. MT5 запущен", "2. Algo Trading включён", "3. Символ существует", "4. Лот допустим", "5. WebRequest разрешён"])


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
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


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
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:usd|pnl)?", str(message or ""), re.I)
    return fmt_pnl(match.group(1), "USD") if match else "нет данных"


def extract_closed_part(message: Optional[str]) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(message or ""))
    return f"{match.group(1)}%" if match else "нет данных"


def format_error(title: str, details: str) -> str:
    return "\n".join(["⚠️ " + title.upper(), fmt_divider(), "", short_text(details, 400)])


def parse_telegram_update(update: dict) -> tuple[Optional[str], Optional[str]]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    return (str(chat_id) if chat_id is not None else None, text)
