from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


BERLIN_TZ = ZoneInfo("Europe/Berlin")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clean_mode_enabled() -> bool:
    return _env_bool("TELEGRAM_TRADE_CLEAN_MODE", True)


def debug_trade_events_enabled() -> bool:
    return _env_bool("TELEGRAM_DEBUG_TRADE_EVENTS", False)


def first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value) -> str:
    return str(value or "").strip()


def _event_type(payload: dict) -> str:
    return _str(first_present(payload.get("event_type"), payload.get("type"), payload.get("event"))).lower()


def _tp_index(payload: dict, event_type: str) -> Optional[int]:
    value = first_present(payload.get("tp_index"), payload.get("tp"), payload.get("target_index"))
    try:
        if value is not None and value != "":
            return int(value)
    except (TypeError, ValueError):
        pass
    text = event_type.lower()
    if "tp1" in text:
        return 1
    if "tp2" in text:
        return 2
    if "tp3" in text:
        return 3
    return None


def _realized_net(payload: dict) -> Optional[float]:
    explicit = _num(first_present(payload.get("realized_net"), payload.get("total_net"), payload.get("net_profit"), payload.get("profit_money")))
    if explicit is not None:
        return explicit
    profit = _num(payload.get("profit"))
    commission = _num(payload.get("commission"))
    swap = _num(payload.get("swap"))
    if profit is not None:
        return profit + (commission or 0.0) + (swap or 0.0)
    return None


def trade_uid_from_payload(payload: dict) -> str:
    explicit = first_present(payload.get("trade_uid"), payload.get("tradeUid"))
    if explicit:
        return str(explicit)
    bot_id = first_present(payload.get("bot_id"), payload.get("botId"), "unknown_bot")
    symbol = first_present(payload.get("symbol"), payload.get("mt5_symbol"), "unknown_symbol")
    magic = first_present(payload.get("magic"), payload.get("magic_number"), "")
    position_id = first_present(payload.get("position_id"), payload.get("position_ticket"), payload.get("position"))
    if position_id:
        return "|".join([str(bot_id), str(symbol), str(magic), str(position_id)])
    open_time = first_present(payload.get("open_time"), payload.get("opened_at"), payload.get("time"), "")
    side = first_present(payload.get("side"), "")
    basis = "|".join([str(bot_id), str(symbol), str(magic), str(open_time), str(side)])
    return "trade_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def event_id_from_payload(payload: dict, normalized_type: str, trade_uid: str) -> str:
    explicit = first_present(payload.get("event_id"), payload.get("eventId"), payload.get("dedupe_key"))
    if explicit:
        return str(explicit)
    basis = "|".join(
        [
            str(first_present(payload.get("bot_id"), payload.get("botId"), "")),
            str(first_present(payload.get("symbol"), payload.get("mt5_symbol"), "")),
            str(trade_uid or ""),
            str(normalized_type or ""),
            str(first_present(payload.get("deal_ticket"), payload.get("ticket"), "")),
            str(first_present(payload.get("close_volume"), payload.get("closed_volume"), payload.get("volume"), "")),
            str(first_present(payload.get("close_price"), payload.get("exit_price"), payload.get("price"), "")),
            str(first_present(payload.get("time"), payload.get("deal_time"), payload.get("close_time"), "")),
            str(first_present(payload.get("tp_index"), "")),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def normalizeNativeTradeEvent(payload: dict) -> dict:
    payload = dict(payload or {})
    raw_type = _event_type(payload)
    tp_index = _tp_index(payload, raw_type)
    aliases = {
        "opened": "trade_opened",
        "trade_opened": "trade_opened",
        "open": "trade_opened",
        "tp1_be": "trade_tp1_be",
        "tp1_hit": "trade_tp1_be",
        "tp1_taken": "trade_tp1_be",
        "tp1_closed": "trade_tp1_be",
        "tp2_hit": "trade_partial_silent",
        "tp2_taken": "trade_partial_silent",
        "tp2_closed": "trade_partial_silent",
        "partial_close_tp2": "trade_partial_silent",
        "tp3_hit": "trade_partial_silent",
        "tp3_taken": "trade_partial_silent",
        "tp3_closed": "trade_partial_silent",
        "partial_close_tp3": "trade_partial_silent",
        "partial_close": "trade_tp1_be" if tp_index == 1 else "trade_partial_silent",
        "be_moved": "silent",
        "closed": "trade_closed",
        "trade_closed": "trade_closed",
        "position_closed": "trade_closed",
        "closed_by_signal": "trade_closed",
        "closed_profit": "trade_closed",
        "closed_loss": "trade_closed",
        "open_failed": "trade_execution_error",
        "trade_send_failed": "trade_execution_error",
        "ordercheck_failed": "trade_execution_error",
        "order_sent_but_position_not_found": "trade_execution_error",
        "close_failed": "trade_execution_error",
        "invalid_volume": "trade_execution_error",
        "invalid_stops": "trade_execution_error",
        "not_enough_money": "trade_execution_error",
        "market_closed": "trade_execution_error",
        "trading_disabled": "trade_execution_error",
        "error": "trade_execution_error",
    }
    normalized_type = aliases.get(raw_type, "silent")
    if normalized_type == "trade_partial_silent" and tp_index == 1:
        normalized_type = "trade_tp1_be"

    template = {
        "trade_opened": "opened",
        "trade_tp1_be": "tp1_be",
        "trade_closed": "closed",
        "trade_execution_error": "execution_error",
    }.get(normalized_type, "silent")

    should_notify = normalized_type in {"trade_opened", "trade_tp1_be", "trade_closed", "trade_execution_error"}
    if clean_mode_enabled() and normalized_type not in {"trade_opened", "trade_tp1_be", "trade_closed", "trade_execution_error"}:
        should_notify = False
    if not clean_mode_enabled() and debug_trade_events_enabled() and normalized_type in {"trade_partial_silent", "silent"}:
        should_notify = True
    if clean_mode_enabled() and debug_trade_events_enabled() and normalized_type == "trade_partial_silent":
        should_notify = True

    trade_uid = trade_uid_from_payload(payload)
    event_id = event_id_from_payload(payload, normalized_type, trade_uid)
    return {
        "eventId": event_id,
        "normalizedType": normalized_type,
        "shouldNotifyTelegram": should_notify,
        "shouldStore": True,
        "telegramTemplate": template,
        "tradeUid": trade_uid,
        "botId": first_present(payload.get("bot_id"), payload.get("botId")),
        "symbol": first_present(payload.get("symbol"), payload.get("mt5_symbol")),
        "side": payload.get("side"),
        "tpIndex": tp_index,
        "rawEventType": raw_type,
        "rawPayload": payload,
    }


def accounting_event_type(payload: dict, normalized: dict) -> str:
    raw_type = str(normalized.get("rawEventType") or "").lower()
    normalized_type = normalized.get("normalizedType")
    tp_index = normalized.get("tpIndex")
    if normalized_type == "trade_opened":
        return "opened"
    if normalized_type == "trade_tp1_be":
        return "tp1_closed"
    if normalized_type == "trade_partial_silent":
        if tp_index == 3:
            return "tp3_closed"
        return "tp2_closed"
    if normalized_type == "trade_closed":
        return "position_closed"
    if normalized_type == "trade_execution_error":
        return raw_type if raw_type in {"open_failed", "close_failed", "rejected", "close_rejected", "error"} else "open_failed"
    if raw_type == "be_moved":
        return "be_moved"
    return raw_type or "unknown"


def _price(value) -> Optional[str]:
    number = _num(value)
    return f"{number:.2f}" if number is not None else None


def _money(value) -> Optional[str]:
    number = _num(value)
    if number is None:
        return None
    sign = "+" if number > 0 else "-" if number < 0 else ""
    return f"{sign}{abs(number):.2f}"


def _percent(value) -> Optional[str]:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.0f}%" if number == int(number) else f"{number:.1f}%"


def _time_text(value=None) -> str:
    parsed = None
    if value:
        text = str(value).replace("Z", "+00:00")
        for parser in (
            lambda: datetime.fromisoformat(text),
            lambda: datetime.strptime(text, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc),
            lambda: datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
        ):
            try:
                parsed = parser()
                break
            except ValueError:
                continue
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BERLIN_TZ).strftime("%H:%M %d.%m.%Y")


def _duration_minutes(opened_at, closed_at) -> Optional[int]:
    if not opened_at:
        return None
    try:
        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00")) if closed_at else datetime.now(timezone.utc)
    except ValueError:
        closed = datetime.now(timezone.utc)
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=timezone.utc)
    return max(0, int((closed.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60))


def _tp_rows(payload: dict, opened: bool = False) -> list[str]:
    rows: list[str] = []
    entry = _num(first_present(payload.get("entry"), payload.get("entry_price")))
    for idx in (1, 2, 3):
        level = first_present(payload.get(f"tp{idx}"), payload.get(f"tp{idx}_price"))
        if _num(level) is None:
            continue
        percent = first_present(payload.get(f"tp{idx}_percent"), payload.get(f"tp{idx}_closed_percent"))
        if percent is None and idx == 1:
            percent = payload.get("closed_percent")
        if opened:
            rows.append(f"TP{idx}: {_price(level)}")
            details = []
            if percent is not None:
                details.append(f"{_percent(percent)} позиции")
            if entry is not None:
                details.append(f"{abs(_num(level) - entry):.1f} pts")
            if details:
                rows.append("    " + ", ".join(details))
            rows.append("")
        else:
            net = first_present(payload.get(f"tp{idx}_net"), payload.get(f"tp{idx}_profit"))
            if net is not None:
                rows.append(f"TP{idx}: {_money(net)}")
    return rows


def format_clean_trade_message(payload: dict, normalized: Optional[dict] = None, daily_stats: Optional[dict] = None) -> str:
    payload = dict(payload or {})
    normalized = normalized or normalizeNativeTradeEvent(payload)
    template = normalized.get("telegramTemplate")
    symbol = first_present(payload.get("symbol"), normalized.get("symbol"), "n/a")
    side = str(first_present(payload.get("side"), normalized.get("side"), "n/a")).upper()
    lot = _num(first_present(payload.get("lot"), payload.get("lots"), payload.get("lot_initial")))
    header = f"{symbol} | {side}" + (f" | {lot:.2f} lot" if lot is not None else "")
    entry = first_present(payload.get("entry"), payload.get("entry_price"))
    sl = first_present(payload.get("sl"), payload.get("sl_price"))
    exit_price = first_present(payload.get("exit_price"), payload.get("close_price"), payload.get("price"))

    if template == "opened":
        lines = ["🟢 СДЕЛКА ОТКРЫТА", "", f"📊 {header}", ""]
        if _price(entry):
            lines.append(f"Entry: {_price(entry)}")
        if _price(sl):
            lines.append(f"SL: {_price(sl)}")
        tp_rows = _tp_rows(payload, opened=True)
        if tp_rows:
            lines.extend(["", "🎯 ТЕЙКИ И РАСЧЁТ", "", *tp_rows])
        return "\n".join(line for line in lines if line is not None).rstrip()

    if template == "tp1_be":
        net = first_present(payload.get("realized_net"), payload.get("tp1_net"), payload.get("profit_money"), payload.get("profit"))
        closed_percent = first_present(payload.get("closed_percent"), payload.get("tp1_percent"))
        volume = first_present(payload.get("close_volume"), payload.get("closed_volume"), payload.get("tp1_volume"), payload.get("volume"))
        remaining = first_present(payload.get("remaining_percent"), payload.get("remaining_volume_percent"))
        if remaining is None and _num(closed_percent) is not None:
            remaining = max(0.0, 100.0 - _num(closed_percent))
        lines = ["🎯 TP1 ВЗЯТ", "", f"📊 {symbol} | {side}", ""]
        tp1 = first_present(payload.get("tp1"), payload.get("tp1_price"), exit_price)
        if _price(tp1):
            lines.append(f"TP1: {_price(tp1)}")
        if closed_percent is not None:
            lines.append(f"Закрыто: {_percent(closed_percent)} позиции")
        if _num(volume) is not None:
            lines.append(f"Объём: {_num(volume):.2f} lot")
        if _money(net):
            lines.extend(["", f"💰 Зафиксировано: {_money(net)}"])
        r_value = first_present(payload.get("r"), payload.get("profit_r"), payload.get("r_multiple"))
        if _num(r_value) is not None:
            lines.append(f"📊 R: {_num(r_value):.2f}R")
        lines.extend(["", "🛡 SL BE"])
        if remaining is not None:
            lines.append(f"Остаток в рынке: {_percent(remaining)}")
        lines.extend(["", f"🕐 {_time_text(first_present(payload.get('deal_time'), payload.get('time')))}"])
        return "\n".join(lines)

    if template == "closed":
        total = first_present(payload.get("total_net"), payload.get("realized_net"), payload.get("total_profit"), payload.get("profit_money"), payload.get("profit"))
        total_num = _num(total) or 0.0
        title = "СДЕЛКА ЗАКРЫТА ПРОФИТ" if total_num >= 0 else "СДЕЛКА ЗАКРЫТА УБЫТОК"
        lines = [title, "", f"📊 {header}", ""]
        if _price(entry):
            lines.append(f"Entry: {_price(entry)}")
        if _price(exit_price):
            lines.append(f"Exit: {_price(exit_price)}")
        tp_rows = _tp_rows(payload, opened=False)
        if tp_rows:
            lines.extend(["", *tp_rows])
        reason = first_present(payload.get("close_reason"), payload.get("reason"))
        if reason:
            lines.extend(["", f"Причина: {reason}"])
        lines.extend(["", f"💰 Итого: {_money(total_num)}"])
        duration = first_present(payload.get("duration_minutes"), None)
        if duration is None:
            duration = _duration_minutes(first_present(payload.get("opened_at"), payload.get("open_time")), first_present(payload.get("closed_at"), payload.get("close_time"), payload.get("time")))
        if duration is not None:
            lines.append(f"Время: {duration} мин")
        if daily_stats:
            day = _money(daily_stats.get("closed_pnl"))
            if day:
                lines.extend(["", f"📅 День: {day} | {daily_stats.get('wins', 0)}W / {daily_stats.get('losses', 0)}L"])
        return "\n".join(lines)

    if template == "execution_error":
        reason = first_present(payload.get("message"), payload.get("reason"), payload.get("retcode_description"), "Execution failed")
        lines = [
            "🔴 ОШИБКА ИСПОЛНЕНИЯ",
            "",
            f"📍 Символ: {symbol}",
            f"🤖 Бот: {first_present(payload.get('bot_id'), normalized.get('botId'), 'n/a')}",
            f"Событие: {first_present(normalized.get('rawEventType'), payload.get('event_type'), 'error')}",
            "",
            "Причина:",
            str(reason),
            "",
        ]
        for label, key in (("Side", "side"), ("Лот", "lot"), ("Entry", "entry"), ("SL", "sl"), ("TP1", "tp1"), ("TP2", "tp2"), ("TP3", "tp3"), ("Retcode", "retcode"), ("LastError", "last_error")):
            value = payload.get(key)
            if value is not None and value != "":
                lines.append(f"{label}: {value}")
        lines.extend(["", "Проверить:", "1. AutoTrading", "2. Symbol", "3. Volume", "4. SL/TP distance", "5. Margin"])
        return "\n".join(lines)

    return ""
