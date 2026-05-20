from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .database import db


LAB_SYMBOLS = ("NAS100", "SP500", "US500", "DJ30", "XAUUSD", "BTCUSD", "GER40")


def canonical_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return "UNKNOWN"
    if "US500" in text or "SP500" in text:
        return "SP500"
    for symbol in ("NAS100", "DJ30", "XAUUSD", "BTCUSD", "GER40"):
        if symbol in text:
            return symbol
    return text.split(".")[0]


def load_strategy_trades(symbol: Optional[str] = None, bot_id: Optional[str] = None, limit: int = 10000) -> list[dict]:
    selector_symbol = canonical_symbol(symbol) if symbol else None
    selector_bot = str(bot_id or "").strip()
    seen: set[str] = set()
    trades: list[dict] = []
    with db() as conn:
        journal = conn.execute(
            """
            SELECT * FROM native_trade_journal
            ORDER BY COALESCE(closed_at, opened_at, created_at) ASC, id ASC
            LIMIT ?
            """,
            (max(1, int(limit or 10000)),),
        ).fetchall()
        closed = conn.execute(
            """
            SELECT * FROM native_mt5_closed_trades
            ORDER BY COALESCE(closed_at, created_at) ASC, created_at ASC
            LIMIT ?
            """,
            (max(1, int(limit or 10000)),),
        ).fetchall()
        deals = conn.execute(
            """
            SELECT * FROM deal_reports
            ORDER BY COALESCE(closed_at, created_at) ASC, id ASC
            LIMIT ?
            """,
            (max(1, int(limit or 10000)),),
        ).fetchall()
        history = conn.execute(
            """
            SELECT * FROM history_deals
            ORDER BY COALESCE(deal_time, updated_at) ASC, id ASC
            LIMIT ?
            """,
            (max(1, int(limit or 10000)),),
        ).fetchall()
    for raw in journal:
        trade = _from_journal(dict(raw))
        _append_trade(trades, seen, trade, selector_symbol, selector_bot)
    for raw in closed:
        trade = _from_closed(dict(raw))
        _append_trade(trades, seen, trade, selector_symbol, selector_bot)
    for raw in deals:
        trade = _from_deal_report(dict(raw))
        _append_trade(trades, seen, trade, selector_symbol, selector_bot)
    for raw in history:
        trade = _from_history_deal(dict(raw))
        _append_trade(trades, seen, trade, selector_symbol, selector_bot)
    trades.sort(key=lambda item: item.get("close_time") or item.get("entry_time") or "")
    return trades


def save_strategy_lab_run(request: dict, result: dict) -> int | None:
    payload = json.dumps(result, ensure_ascii=False, default=str)
    with db() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO strategy_lab_runs
                    (symbol, bot_id, optimize, include_bias_filter, request_payload, result_payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.get("symbol"),
                    request.get("bot_id"),
                    1 if request.get("optimize") else 0,
                    1 if request.get("include_bias_filter") else 0,
                    json.dumps(request, ensure_ascii=False, default=str),
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cur.lastrowid)
        except Exception:
            return None


def latest_strategy_lab_run() -> Optional[dict]:
    with db() as conn:
        try:
            row = conn.execute(
                """
                SELECT * FROM strategy_lab_runs
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        except Exception:
            return None
    if not row:
        return None
    try:
        payload = json.loads(row["result_payload"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("run_id", row["id"])
    payload.setdefault("created_at", row["created_at"])
    return payload


def _append_trade(trades: list[dict], seen: set[str], trade: dict, symbol: Optional[str], bot_id: str) -> None:
    if not trade.get("symbol") or trade.get("profit") is None:
        return
    if symbol and canonical_symbol(trade.get("symbol")) != symbol:
        return
    if bot_id and bot_id not in str(trade.get("bot_id") or ""):
        return
    key = str(trade.get("dedupe_key") or trade.get("trade_uid") or trade.get("ticket") or "")
    if key and key in seen:
        return
    if key:
        seen.add(key)
    trades.append(trade)


def _from_journal(row: dict) -> dict:
    profit = _first_number(row, "total_net", "profit", "total_profit")
    entry = _first_number(row, "entry", "entry_price")
    sl = _first_number(row, "sl", "sl_price")
    tp1 = _first_number(row, "tp1", "tp1_price")
    tp2 = _first_number(row, "tp2", "tp2_price")
    tp3 = _first_number(row, "tp3", "tp3_price")
    history_source = row.get("close_reason") == "history_sync"
    dedupe_key = (
        row.get("position_id") or row.get("deal_ticket") or row.get("ticket") or row.get("trade_uid")
        if history_source
        else row.get("trade_uid") or row.get("ticket") or row.get("position_id") or row.get("deal_ticket")
    )
    return {
        "source": row.get("source") or "native_trade_journal",
        "dedupe_key": dedupe_key,
        "trade_uid": row.get("trade_uid"),
        "ticket": row.get("ticket"),
        "symbol": canonical_symbol(row.get("symbol")),
        "bot_id": row.get("bot_id"),
        "side": row.get("side"),
        "entry_time": row.get("opened_at"),
        "close_time": row.get("closed_at"),
        "entry_price": entry,
        "exit_price": _first_number(row, "exit_price"),
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "profit": profit,
        "commission": _first_number(row, "total_commission", "commission"),
        "swap": _first_number(row, "total_swap", "swap"),
        "result_money": profit,
        "tp1_hit": bool(row.get("tp1_done") or row.get("tp1_net") is not None),
        "tp2_hit": bool(row.get("tp2_done") or row.get("tp2_net") is not None),
        "tp3_hit": bool(row.get("tp3_done") or row.get("tp3_net") is not None),
        "be_activated": bool(row.get("be_done")),
        "duration_seconds": _first_int(row, "duration_seconds"),
        "payload": _payload(row),
    }


def _from_closed(row: dict) -> dict:
    return {
        "source": "native_mt5_closed_trades",
        "trade_uid": row.get("trade_uid") or row.get("trade_key"),
        "ticket": row.get("trade_key"),
        "symbol": canonical_symbol(row.get("symbol")),
        "bot_id": row.get("bot_id"),
        "side": row.get("side"),
        "entry_time": row.get("opened_at"),
        "close_time": row.get("closed_at") or row.get("created_at"),
        "entry_price": _first_number(row, "entry"),
        "exit_price": _first_number(row, "exit_price"),
        "stop_loss": _first_number(row, "sl"),
        "tp1": _first_number(row, "tp1"),
        "tp2": _first_number(row, "tp2"),
        "tp3": _first_number(row, "tp3"),
        "profit": _first_number(row, "profit"),
        "result_money": _first_number(row, "profit"),
        "tp1_hit": bool(row.get("tp1_done")),
        "tp2_hit": bool(row.get("tp2_done")),
        "tp3_hit": bool(row.get("tp3_done")),
        "be_activated": bool(row.get("be_done")),
        "payload": _payload(row),
    }


def _from_deal_report(row: dict) -> dict:
    profit = _first_number(row, "net_profit", "profit")
    return {
        "source": "deal_reports",
        "trade_uid": row.get("deal_ticket"),
        "ticket": row.get("deal_ticket"),
        "symbol": canonical_symbol(row.get("symbol")),
        "bot_id": row.get("comment"),
        "side": row.get("side"),
        "entry_time": row.get("opened_at"),
        "close_time": row.get("closed_at") or row.get("created_at"),
        "entry_price": _first_number(row, "entry_price"),
        "exit_price": _first_number(row, "exit_price"),
        "profit": profit,
        "result_money": profit,
        "payload": _payload(row),
    }


def _from_history_deal(row: dict) -> dict:
    profit = _first_number(row, "net", "profit")
    return {
        "source": "history_deals",
        "dedupe_key": row.get("position_id") or row.get("deal_ticket"),
        "trade_uid": row.get("position_id") or row.get("deal_ticket"),
        "ticket": row.get("deal_ticket"),
        "symbol": canonical_symbol(row.get("symbol")),
        "bot_id": row.get("bot_id"),
        "side": row.get("side"),
        "entry_time": row.get("deal_time"),
        "close_time": row.get("deal_time") or row.get("updated_at"),
        "exit_price": _first_number(row, "price"),
        "profit": profit,
        "result_money": profit,
        "payload": _payload(row),
    }


def _first_number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_int(row: dict, *keys: str) -> int | None:
    value = _first_number(row, *keys)
    return int(value) if value is not None else None


def _payload(row: dict) -> dict:
    raw = row.get("payload")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
