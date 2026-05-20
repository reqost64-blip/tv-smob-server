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
        "tp2_hit": "trade_tp2",
        "tp2_taken": "trade_tp2",
        "tp2_closed": "trade_tp2",
        "tp2_silent": "trade_tp2",
        "partial_close_tp2": "trade_tp2",
        "tp3_hit": "trade_tp3",
        "tp3_taken": "trade_tp3",
        "tp3_closed": "trade_tp3",
        "tp3_silent": "trade_partial_silent",
        "partial_close_tp3": "trade_partial_silent",
        "partial_close": "trade_tp1_be" if tp_index == 1 else "trade_partial_silent",
        "be_moved": "trade_be",
        "sl_be": "trade_be",
        "break_even": "trade_be",
        "breakeven": "trade_be",
        "closed": "trade_closed",
        "trade_closed": "trade_closed",
        "position_closed": "trade_closed",
        "closed_by_signal": "trade_closed",
        "closed_profit": "trade_closed_profit",
        "closed_loss": "trade_closed_loss",
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
        "execution_error": "trade_execution_error",
        "native-history": "history_sync_silent",
        "history_sync": "history_sync_silent",
    }
    normalized_type = aliases.get(raw_type, "silent")
    if normalized_type == "trade_partial_silent" and tp_index == 1:
        normalized_type = "trade_tp1_be"
    elif normalized_type == "trade_partial_silent" and tp_index == 2:
        normalized_type = "trade_tp2"

    template = {
        "trade_opened": "opened",
        "trade_tp1_be": "tp1_be",
        "trade_tp2": "tp2",
        "trade_tp3": "closed",
        "trade_be": "be",
        "trade_closed": "closed",
        "trade_closed_profit": "closed",
        "trade_closed_loss": "closed",
        "trade_execution_error": "execution_error",
    }.get(normalized_type, "silent")

    notify_types = {"trade_opened", "trade_tp1_be", "trade_tp2", "trade_tp3", "trade_be", "trade_closed", "trade_closed_profit", "trade_closed_loss", "trade_execution_error"}
    should_notify = normalized_type in notify_types
    if bool(payload.get("telegram_silent")) and normalized_type != "trade_tp2":
        should_notify = False
    if clean_mode_enabled() and normalized_type not in notify_types:
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
    if normalized_type == "trade_tp2":
        return "tp2_closed"
    if normalized_type == "trade_tp3":
        return "tp3_closed"
    if normalized_type == "trade_be":
        return "be_moved"
    if normalized_type == "trade_partial_silent":
        if tp_index == 3:
            return "tp3_closed"
        return "tp2_closed"
    if normalized_type in {"trade_closed", "trade_closed_profit", "trade_closed_loss"}:
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
    return f"{sign}€{abs(number):.2f}"


def _percent(value) -> Optional[str]:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.0f}%" if number == int(number) else f"{number:.1f}%"


def _dash(value: Optional[str]) -> str:
    return value if value not in (None, "") else "—"


def _price_text(value) -> str:
    return _dash(_price(value))


def _money_text(value) -> str:
    return _dash(_money(value))


def _percent_text(value) -> str:
    return _dash(_percent(value))


def _r_text(value, *, signed: bool = True) -> str:
    number = _num(value)
    if number is None:
        return "—"
    sign = "+" if signed and number > 0 else "-" if signed and number < 0 else ""
    return f"{sign}{abs(number):.2f}R"


def _boolish(value) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y", "done", "hit", "taken", "closed"}:
        return True
    if text in {"0", "false", "no", "off", "n", "disabled", "none"}:
        return False
    return None


def _tp_enabled(payload: dict, idx: int) -> bool:
    explicit = first_present(
        payload.get(f"tp{idx}_enabled"),
        payload.get(f"use_tp{idx}"),
        payload.get(f"enable_tp{idx}"),
    )
    if _boolish(explicit) is False:
        return False
    return True


def _tp_level(payload: dict, idx: int):
    return first_present(payload.get(f"tp{idx}"), payload.get(f"tp{idx}_price"))


def _tp_configured(payload: dict, idx: int, current_tp: Optional[int] = None, full_close: bool = False) -> bool:
    if not _tp_enabled(payload, idx):
        return False
    if current_tp == idx:
        return True
    if _num(_tp_level(payload, idx)) not in (None, 0.0):
        return True
    if any(payload.get(key) is not None for key in (f"tp{idx}_done", f"tp{idx}_hit", f"tp{idx}_net", f"tp{idx}_profit")):
        return True
    return bool(full_close and idx == 1 and payload.get("tp1_done"))


def _tp_done(payload: dict, idx: int, current_tp: Optional[int] = None, full_close: bool = False) -> bool:
    explicit = first_present(
        payload.get(f"tp{idx}_done"),
        payload.get(f"tp{idx}_hit"),
        payload.get(f"tp{idx}_taken"),
        payload.get(f"tp{idx}_closed"),
    )
    explicit_bool = _boolish(explicit)
    if explicit_bool is not None:
        return explicit_bool
    if current_tp is not None and idx <= current_tp:
        return True
    if full_close and idx == 3 and _tp_configured(payload, idx, current_tp=current_tp, full_close=full_close):
        return True
    return False


def tp_progress_lines(payload: dict, current_tp: Optional[int] = None, full_close: bool = False) -> list[str]:
    rows: list[str] = []
    for idx in (1, 2, 3):
        if not _tp_configured(payload, idx, current_tp=current_tp, full_close=full_close):
            continue
        rows.append(f"TP{idx} {'✅' if _tp_done(payload, idx, current_tp=current_tp, full_close=full_close) else '⬜'}")
    return rows


def _remaining_percent(payload: dict, closed_percent=None):
    remaining = first_present(payload.get("remaining_percent"), payload.get("remaining_volume_percent"))
    if remaining is None and _num(closed_percent) is not None:
        remaining = max(0.0, 100.0 - _num(closed_percent))
    return remaining


def _event_time(payload: dict) -> str:
    return _time_text(first_present(payload.get("deal_time"), payload.get("time"), payload.get("closed_at"), payload.get("close_time")))


def _current_tp_price(payload: dict, idx: int, exit_price=None):
    return first_present(payload.get(f"tp{idx}"), payload.get(f"tp{idx}_price"), exit_price)


def _profit_value(payload: dict, tp_key: Optional[str] = None):
    keys = []
    if tp_key:
        keys.extend([payload.get(f"{tp_key}_net"), payload.get(f"{tp_key}_profit")])
    keys.extend([payload.get("realized_net"), payload.get("profit_money"), payload.get("profit")])
    return first_present(*keys)


def _total_profit_value(payload: dict):
    return first_present(
        payload.get("accumulated_profit"),
        payload.get("running_total"),
        payload.get("total_net"),
        payload.get("total_profit"),
        payload.get("net_profit"),
        payload.get("profit_money"),
        payload.get("profit"),
    )


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


def format_clean_trade_message(payload: dict, normalized: Optional[dict] = None, daily_stats: Optional[dict] = None) -> str:
    payload = dict(payload or {})
    normalized = normalized or normalizeNativeTradeEvent(payload)
    template = normalized.get("telegramTemplate")
    symbol = first_present(payload.get("symbol"), normalized.get("symbol"), "—")
    side = str(first_present(payload.get("side"), normalized.get("side"), "—")).upper()
    lot = _num(first_present(payload.get("lot"), payload.get("lots"), payload.get("lot_initial")))
    header = f"{symbol} | {side}"
    entry = first_present(payload.get("entry"), payload.get("entry_price"))
    sl = first_present(payload.get("sl"), payload.get("sl_price"))
    exit_price = first_present(payload.get("exit_price"), payload.get("close_price"), payload.get("price"))

    if template == "opened":
        lines = [
            "✅ СДЕЛКА ОТКРЫТА",
            "",
            header,
            f"Вход: {_price_text(entry)}",
            f"SL: {_price_text(sl)}",
        ]
        tp_rows = [
            f"TP{idx} ⬜ {_price_text(_tp_level(payload, idx))}"
            for idx in (1, 2, 3)
            if _tp_configured(payload, idx)
        ]
        if tp_rows:
            lines.extend(["", *tp_rows])
        risk = first_present(payload.get("risk_r"), payload.get("risk"), payload.get("r"), payload.get("r_multiple"))
        lines.extend(
            [
                "",
                f"Риск: {_r_text(risk, signed=False)}",
                f"Объём: {_dash(f'{lot:.2f} lot' if lot is not None else None)}",
                "",
                f"🕒 {_event_time(payload)}",
            ]
        )
        return "\n".join(lines)

    if template == "tp1_be":
        closed_percent = first_present(payload.get("closed_percent"), payload.get("tp1_percent"), payload.get("tp1_closed_percent"))
        remaining = _remaining_percent(payload, closed_percent)
        r_value = first_present(payload.get("r"), payload.get("profit_r"), payload.get("r_multiple"))
        lines = [
            "🎯 TP1 ВЗЯТ ✅",
            "",
            header,
            f"TP1: {_price_text(_current_tp_price(payload, 1, exit_price))}",
            "",
            f"Закрыто: {_percent_text(closed_percent)}",
            f"Прибыль: {_money_text(_profit_value(payload, 'tp1'))}",
            f"R: {_r_text(r_value)}",
        ]
        progress = tp_progress_lines(payload, current_tp=1)
        if progress:
            lines.extend(["", *progress])
        lines.extend(
            [
                "",
                "SL → BE",
                f"Остаток: {_percent_text(remaining)}",
                "",
                f"🕒 {_event_time(payload)}",
            ]
        )
        return "\n".join(lines)

    if template == "tp2":
        closed_percent = first_present(payload.get("closed_percent"), payload.get("tp2_percent"), payload.get("tp2_closed_percent"))
        remaining = _remaining_percent(payload, closed_percent)
        lines = [
            "🎯 TP2 ВЗЯТ ✅",
            "",
            header,
            f"TP2: {_price_text(_current_tp_price(payload, 2, exit_price))}",
            "",
            f"Закрыто: {_percent_text(closed_percent)}",
            f"Прибыль: {_money_text(_profit_value(payload, 'tp2'))}",
            f"Всего: {_money_text(_total_profit_value(payload))}",
        ]
        progress = tp_progress_lines(payload, current_tp=2)
        if progress:
            lines.extend(["", *progress])
        lines.extend(
            [
                "",
                f"Остаток: {_percent_text(remaining)}",
                "",
                f"🕒 {_event_time(payload)}",
            ]
        )
        return "\n".join(lines)

    if template == "be":
        closed_percent = first_present(payload.get("closed_percent"), payload.get("tp1_percent"), payload.get("tp1_closed_percent"))
        remaining = _remaining_percent(payload, closed_percent)
        new_sl = first_present(payload.get("new_sl"), payload.get("sl"), payload.get("sl_price"), payload.get("entry"), payload.get("entry_price"))
        lines = [
            "🛡 SL В БУ",
            "",
            header,
            "",
            "SL перенесён в безубыток",
            f"Новый SL: {_price_text(new_sl)}",
        ]
        progress = tp_progress_lines(payload)
        if progress:
            lines.extend(["", *progress])
        lines.extend(
            [
                "",
                f"Остаток: {_percent_text(remaining)}",
                "",
                f"🕒 {_event_time(payload)}",
            ]
        )
        return "\n".join(lines)

    if template == "closed":
        total = _total_profit_value(payload)
        total_num = _num(total)
        r_value = first_present(payload.get("r"), payload.get("profit_r"), payload.get("r_multiple"))
        reason_text = str(first_present(payload.get("close_reason"), payload.get("reason"), normalized.get("rawEventType"), "")).lower()
        raw_type = str(first_present(normalized.get("rawEventType"), payload.get("event_type"), "")).lower()
        is_stop_loss = normalized.get("normalizedType") == "trade_closed_loss" or (total_num is not None and total_num < 0) or any(marker in reason_text for marker in ("sl", "stop", "loss"))
        current_tp = 3 if "tp3" in raw_type or "tp3" in reason_text else None
        if is_stop_loss:
            lines = [
                "🛑 STOP LOSS",
                "",
                header,
                "",
                f"Убыток: {_money_text(total)}",
                f"R: {_r_text(r_value)}",
            ]
            progress = tp_progress_lines(payload)
            if progress:
                lines.extend(["", *progress])
        else:
            lines = [
                "✅ СДЕЛКА ЗАКРЫТА",
                "",
                header,
                "",
                f"Итог: {_money_text(total)}",
                f"R: {_r_text(r_value)}",
            ]
            progress = tp_progress_lines(payload, current_tp=current_tp, full_close=True)
            if progress:
                lines.extend(["", *progress])
            duration = first_present(payload.get("duration_minutes"), None)
            if duration is None:
                duration = _duration_minutes(
                    first_present(payload.get("opened_at"), payload.get("open_time")),
                    first_present(payload.get("closed_at"), payload.get("close_time"), payload.get("time")),
                )
            lines.extend(["", f"Время в сделке: {_dash(f'{duration}м' if duration is not None else None)}"])
        lines.extend(["", f"🕒 {_event_time(payload)}"])
        return "\n".join(lines)

    if template == "execution_error":
        reason = first_present(payload.get("message"), payload.get("reason"), payload.get("retcode_description"), "Execution failed")
        lines = [
            "ОШИБКА ИСПОЛНЕНИЯ",
            "",
            f"Символ: {symbol}",
            f"Бот: {first_present(payload.get('bot_id'), normalized.get('botId'), '—')}",
            f"Событие: {first_present(normalized.get('rawEventType'), payload.get('event_type'), 'error')}",
            "",
            "Причина:",
            str(reason),
        ]
        return "\n".join(lines)

    return ""

