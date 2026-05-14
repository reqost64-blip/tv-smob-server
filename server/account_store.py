from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import config
from .database import db
from .models import AccountSnapshot, DealReport, NativeMT5AccountSnapshot, NativeMT5Event, NativeMT5Heartbeat, NativeMT5Screenshot, PositionsSnapshot
from .native_trade_notifications import accounting_event_type, normalizeNativeTradeEvent


NATIVE_EVENT_UPDATES_ACTIVE = {"tp1_closed", "tp2_closed", "tp3_closed", "be_moved"}
NATIVE_EVENT_CLOSES_ACTIVE = {"position_closed", "closed_by_signal"}
NATIVE_REALIZED_EVENT_TYPES = {"position_closed", "closed_by_signal"}
NATIVE_TP_EVENT_TYPES = {"tp1_closed", "tp2_closed", "tp3_closed"}
NATIVE_ERROR_EVENT_TYPES = {"open_failed", "close_failed", "rejected", "close_rejected", "error"}
DEFAULT_NATIVE_ASSETS = ["NAS100", "SP500", "DJ30", "BTCUSD", "GER40"]
DEFAULT_NATIVE_BOTS = [
    {"asset": "NAS100", "bot_id": "NAS100_ORB_VWAP_RSI_OF", "symbol": "NAS100.r", "magic_number": 26043001},
    {"asset": "SP500", "bot_id": "SP500_ORB_VWAP_RSI_OF", "symbol": "US500.r", "magic_number": 26043003},
    {"asset": "DJ30", "bot_id": "DJ30_ORB_VWAP_RSI_OF", "symbol": "DJ30.r", "magic_number": 26043002},
    {"asset": "BTCUSD", "bot_id": "BTCUSD_ORB_VWAP_RSI_OF", "symbol": "BTCUSD", "magic_number": 26043005},
    {"asset": "GER40", "bot_id": "GER40_ORB_VWAP_RSI_OF", "symbol": "GER40", "magic_number": 26043004},
]
BERLIN_TZ = ZoneInfo("Europe/Berlin")


def save_account_snapshot(snapshot: AccountSnapshot) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO account_snapshots
                (balance, equity, margin, free_margin, margin_level, currency,
                 account_login, account_server, trade_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.balance,
                snapshot.equity,
                snapshot.margin,
                snapshot.free_margin,
                snapshot.margin_level,
                snapshot.currency,
                snapshot.account_login,
                snapshot.account_server,
                snapshot.trade_mode,
            ),
        )


def save_positions_snapshot(snapshot: PositionsSnapshot) -> None:
    snapshot_at = snapshot.snapshot_at
    with db() as conn:
        conn.execute("DELETE FROM positions_snapshots")
        for position in snapshot.positions:
            conn.execute(
                """
                INSERT INTO positions_snapshots
                    (ticket, symbol, side, lot, entry_price, current_price, sl, tp,
                     profit, swap, commission, magic, comment, opened_at, snapshot_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                """,
                (
                    position.ticket,
                    position.symbol,
                    position.side,
                    position.lot,
                    position.entry_price,
                    position.current_price,
                    position.sl,
                    position.tp,
                    position.profit,
                    position.swap,
                    position.commission,
                    position.magic,
                    position.comment,
                    position.opened_at,
                    snapshot_at,
                ),
            )


def save_deal_report(report: DealReport) -> None:
    net_profit = report.net_profit
    if net_profit is None:
        net_profit = (report.profit or 0.0) + (report.commission or 0.0) + (report.swap or 0.0)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO deal_reports
                (deal_ticket, position_ticket, symbol, side, lot, entry_price, exit_price,
                 profit, commission, swap, net_profit, opened_at, closed_at, reason, magic, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deal_ticket) DO UPDATE SET
                position_ticket = excluded.position_ticket,
                symbol = excluded.symbol,
                side = excluded.side,
                lot = excluded.lot,
                entry_price = excluded.entry_price,
                exit_price = excluded.exit_price,
                profit = excluded.profit,
                commission = excluded.commission,
                swap = excluded.swap,
                net_profit = excluded.net_profit,
                opened_at = excluded.opened_at,
                closed_at = excluded.closed_at,
                reason = excluded.reason,
                magic = excluded.magic,
                comment = excluded.comment
            """,
            (
                report.deal_ticket,
                report.position_ticket,
                report.symbol,
                report.side,
                report.lot,
                report.entry_price,
                report.exit_price,
                report.profit,
                report.commission,
                report.swap,
                net_profit,
                report.opened_at,
                report.closed_at,
                report.reason,
                report.magic,
                report.comment,
            ),
        )


def save_native_account_snapshot(snapshot: NativeMT5AccountSnapshot) -> None:
    payload = _safe_payload(snapshot)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO native_account_snapshots
                (source, bot_id, symbol, magic_number, balance, equity, margin,
                 free_margin, open_positions, snapshot_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("source") or "mt5_native",
                payload.get("bot_id"),
                payload.get("symbol"),
                payload.get("magic_number"),
                snapshot.balance,
                snapshot.equity,
                snapshot.margin,
                snapshot.free_margin,
                snapshot.open_positions,
                snapshot.time,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        conn.execute(
            """
            INSERT INTO native_mt5_accounts
                (source, symbol, magic_number, balance, equity, margin, free_margin,
                 open_positions, snapshot_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("source") or "mt5_native",
                payload.get("symbol"),
                payload.get("magic_number"),
                snapshot.balance,
                snapshot.equity,
                snapshot.margin,
                snapshot.free_margin,
                snapshot.open_positions,
                snapshot.time,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        _update_native_heartbeat(conn)
        _ensure_native_bot_control(
            conn,
            payload.get("bot_id"),
            payload.get("symbol"),
            payload.get("magic_number"),
            last_account_at=first_present(snapshot.time, now_iso()),
        )


def save_native_event(event: NativeMT5Event) -> bool:
    payload = _safe_payload(event)
    normalized = normalizeNativeTradeEvent(payload)
    original_event_type = str(event.event_type or "").strip().lower()
    event_type = accounting_event_type(payload, normalized)
    payload["original_event_type"] = original_event_type
    payload["event_type"] = event_type
    payload["normalized_type"] = normalized["normalizedType"]
    payload["trade_uid"] = normalized["tradeUid"]
    payload["event_id"] = normalized["eventId"]
    payload["should_notify"] = bool(normalized["shouldNotifyTelegram"])
    payload["tp_index"] = normalized.get("tpIndex")
    payload.setdefault("source", "mt5_native")
    dedupe_key = _native_event_dedupe_key(event, payload, event_type)
    with db() as conn:
        _update_native_heartbeat(conn)
        _ensure_native_bot_control(
            conn,
            event.bot_id,
            event.symbol,
            event.magic_number,
            last_event_at=first_present(event.time, now_iso()),
            last_event_type=event_type,
        )
        if _is_duplicate_native_event(conn, dedupe_key):
            return False
        conn.execute(
            """
            INSERT INTO native_mt5_events
                (event_type, source, bot_id, symbol, magic_number, event_time, payload,
                 event_id, trade_uid, normalized_type, should_notify, telegram_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                event_type,
                payload.get("source"),
                event.bot_id,
                event.symbol,
                event.magic_number,
                event.time,
                json.dumps(payload, ensure_ascii=False, default=str),
                normalized["eventId"],
                normalized["tradeUid"],
                normalized["normalizedType"],
                1 if normalized["shouldNotifyTelegram"] else 0,
            ),
        )
        _record_native_trade_event(conn, event, payload, event_type, dedupe_key)
        if event_type == "opened":
            _upsert_native_active_trade(conn, event, payload, force_new=False)
        elif event_type in NATIVE_EVENT_UPDATES_ACTIVE:
            _upsert_native_active_trade(conn, event, payload, force_new=False)
        elif event_type in NATIVE_EVENT_CLOSES_ACTIVE:
            _close_native_active_trade(conn, event, payload)
        elif event_type in NATIVE_ERROR_EVENT_TYPES:
            _upsert_native_journal_error(conn, event, payload, event_type)
        return True


def latest_account_snapshot() -> Optional[dict]:
    if config.is_native_mt5_only():
        return latest_native_account_snapshot()
    with db() as conn:
        row = conn.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def latest_native_account_snapshot() -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM native_account_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return _native_account_from_row(row)
        row = conn.execute("SELECT * FROM native_mt5_accounts ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            rows = conn.execute(
                """
                SELECT * FROM native_mt5_events
                ORDER BY id DESC LIMIT 50
                """
            ).fetchall()
            for event_row in rows:
                payload = _decode_payload(event_row["payload"])
                if payload.get("balance") is not None and payload.get("equity") is not None:
                    return {
                        "balance": payload.get("balance"),
                        "equity": payload.get("equity"),
                        "margin": payload.get("margin"),
                        "free_margin": payload.get("free_margin"),
                        "margin_level": payload.get("margin_level"),
                        "currency": payload.get("currency") or "€",
                        "bot_id": payload.get("bot_id"),
                        "account_login": payload.get("account_login"),
                        "account_server": payload.get("account_server"),
                        "trade_mode": payload.get("trade_mode"),
                        "open_positions": None,
                        "symbol": payload.get("symbol"),
                        "magic_number": payload.get("magic_number"),
                        "source": payload.get("source") or "mt5_native",
                        "snapshot_at": payload.get("time"),
                        "created_at": event_row["created_at"],
                    }
            return None
        return _native_account_from_row(row)


def get_latest_native_account() -> Optional[dict]:
    return latest_native_account_snapshot()


def _native_account_from_row(row) -> dict:
    data = dict(row)
    payload = _decode_payload(data.get("payload"))
    return {
        "balance": data.get("balance"),
        "equity": data.get("equity"),
        "margin": data.get("margin"),
        "free_margin": data.get("free_margin"),
        "margin_level": payload.get("margin_level"),
        "currency": payload.get("currency") or "€",
        "bot_id": first_present(data.get("bot_id"), payload.get("bot_id")),
        "account_login": payload.get("account_login"),
        "account_server": payload.get("account_server"),
        "trade_mode": payload.get("trade_mode"),
        "open_positions": data.get("open_positions"),
        "symbol": data.get("symbol"),
        "magic_number": data.get("magic_number"),
        "source": data.get("source") or "mt5_native",
        "snapshot_at": data.get("snapshot_at"),
        "created_at": data.get("created_at"),
    }


def current_positions() -> list[dict]:
    if config.is_native_mt5_only():
        return current_native_positions()
    with db() as conn:
        rows = conn.execute("SELECT * FROM positions_snapshots ORDER BY symbol, ticket").fetchall()
        return [dict(row) for row in rows]


def current_native_positions() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_mt5_active_trades
            ORDER BY symbol, magic_number, updated_at DESC
            """
        ).fetchall()
        positions = []
        for row in rows:
            data = dict(row)
            positions.append(
                {
                    "ticket": _synthetic_ticket(data.get("trade_key")),
                    "trade_uid": data.get("trade_uid") or data.get("trade_key"),
                    "symbol": data.get("symbol"),
                    "side": data.get("side"),
                    "lot": data.get("lot"),
                    "bot_id": data.get("bot_id"),
                    "entry": data.get("entry"),
                    "entry_price": data.get("entry"),
                    "current_price": first_present(data.get("current_price"), data.get("exit_price")),
                    "sl": data.get("sl"),
                    "tp": first_present(data.get("tp3"), data.get("tp2"), data.get("tp1")),
                    "tp1": data.get("tp1"),
                    "tp2": data.get("tp2"),
                    "tp3": data.get("tp3"),
                    "tp1_done": bool(data.get("tp1_done")),
                    "tp2_done": bool(data.get("tp2_done")),
                    "tp3_done": bool(data.get("tp3_done")),
                    "be_done": bool(data.get("be_done")),
                    "profit": data.get("profit"),
                    "swap": None,
                    "commission": None,
                    "magic": data.get("magic_number"),
                    "comment": data.get("bot_id") or "mt5_native",
                    "opened_at": data.get("opened_at"),
                    "snapshot_at": data.get("updated_at"),
                    "source": "mt5_native",
                    "status": data.get("status"),
                }
            )
        return positions


def trades_today() -> list[dict]:
    if config.is_native_mt5_only():
        return native_trades_today()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM deal_reports
            WHERE date(COALESCE(closed_at, created_at)) = date('now')
            ORDER BY COALESCE(closed_at, created_at) DESC, id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def native_trades_today() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_mt5_closed_trades
            ORDER BY COALESCE(closed_at, created_at) DESC, created_at DESC
            LIMIT 500
            """
        ).fetchall()
        trades = []
        for row in rows:
            data = dict(row)
            if not _is_today_berlin(first_present(data.get("closed_at"), data.get("created_at"))):
                continue
            ticket = _synthetic_ticket(data.get("trade_key"))
            trades.append(
                {
                    "deal_ticket": ticket,
                    "position_ticket": ticket,
                    "trade_uid": data.get("trade_uid") or data.get("trade_key"),
                    "symbol": data.get("symbol"),
                    "side": data.get("side"),
                    "lot": data.get("lot"),
                    "entry_price": data.get("entry"),
                    "exit_price": data.get("exit_price"),
                    "profit": data.get("profit"),
                    "commission": None,
                    "swap": None,
                    "net_profit": data.get("profit"),
                    "opened_at": data.get("opened_at"),
                    "closed_at": data.get("closed_at"),
                    "reason": data.get("status"),
                    "magic": data.get("magic_number"),
                    "comment": data.get("bot_id") or "mt5_native",
                    "bot_id": data.get("bot_id"),
                    "tp1_done": bool(data.get("tp1_done")),
                    "tp2_done": bool(data.get("tp2_done")),
                    "tp3_done": bool(data.get("tp3_done")),
                    "be_done": bool(data.get("be_done")),
                    "created_at": data.get("created_at"),
                    "source": "mt5_native",
                }
            )
        return trades


def pnl_today() -> dict:
    if config.is_native_mt5_only():
        return native_pnl_today()
    trades = trades_today()
    net_values = [float(trade.get("net_profit") or 0.0) for trade in trades]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value < 0]
    return {
        "trades_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(net_values), 2),
        "best_trade": round(max(net_values), 2) if net_values else None,
        "worst_trade": round(min(net_values), 2) if net_values else None,
    }


def native_pnl_today() -> dict:
    close_profits = []
    tp_pnl = 0.0  # accumulated partial-close profits from TP events
    tp_events = 0
    execution_errors = 0
    with db() as conn:
        event_rows = conn.execute(
            """
            SELECT event_type, event_time, payload, created_at
            FROM native_mt5_events
            ORDER BY id DESC
            LIMIT 5000
            """
        ).fetchall()
        for row in event_rows:
            event_time = first_present(row["event_time"], row["created_at"])
            if not _is_today_berlin(event_time):
                continue
            event_type = str(row["event_type"] or "").strip().lower()
            payload = _decode_payload(row["payload"])
            if event_type in NATIVE_REALIZED_EVENT_TYPES:
                close_profits.append(_payload_profit(payload))
            elif event_type in NATIVE_TP_EVENT_TYPES:
                tp_events += 1
                tp_pnl += _payload_profit(payload)  # TP partial closes are realized P&L
            elif event_type in NATIVE_ERROR_EVENT_TYPES:
                execution_errors += 1

        active_rows = conn.execute("SELECT profit FROM native_mt5_active_trades").fetchall()
        floating_values = [float(row["profit"]) for row in active_rows if row["profit"] is not None]

    closed_count = len(close_profits)
    wins = [value for value in close_profits if value > 0]
    losses = [value for value in close_profits if value < 0]
    active_count = len(active_rows)
    floating_pnl = round(sum(floating_values), 2) if floating_values else (0.0 if active_count == 0 else None)
    if floating_pnl is None and active_count > 0:
        account = latest_native_account_snapshot()
        if account and account.get("balance") is not None and account.get("equity") is not None:
            try:
                floating_pnl = round(float(account["equity"]) - float(account["balance"]), 2)
            except (TypeError, ValueError):
                floating_pnl = None

    realized_available = bool(close_profits) or tp_events == 0
    closed_pnl = round(sum(close_profits) + tp_pnl, 2) if realized_available else None
    total_pnl = None if closed_pnl is None else round(closed_pnl + (floating_pnl or 0.0), 2)
    return {
        "trades_count": closed_count,
        "closed_trades_count": closed_count,
        "wins": len(wins),
        "losses": len(losses),
        "closed_pnl": closed_pnl,
        "floating_pnl": floating_pnl,
        "total_pnl": total_pnl,
        "net_pnl": total_pnl,
        "best_trade": round(max(close_profits), 2) if close_profits else None,
        "worst_trade": round(min(close_profits), 2) if close_profits else None,
        "tp_events": tp_events,
        "execution_errors": execution_errors,
        "realized_available": realized_available,
    }


def last_mt5_heartbeat() -> Optional[str]:
    if config.is_native_mt5_only():
        with db() as conn:
            state = conn.execute(
                "SELECT value FROM native_mt5_state WHERE key = 'last_native_heartbeat_at'"
            ).fetchone()
            if state and state["value"]:
                return state["value"]
            row = conn.execute(
                """
                SELECT MAX(ts) AS ts
                FROM (
                    SELECT created_at AS ts FROM native_account_snapshots
                    UNION ALL
                    SELECT created_at AS ts FROM native_mt5_accounts
                    UNION ALL
                    SELECT created_at AS ts FROM native_mt5_events
                )
                """
            ).fetchone()
            return row["ts"] if row and row["ts"] else None
    account = latest_account_snapshot()
    if account:
        return account.get("created_at")
    with db() as conn:
        row = conn.execute("SELECT MAX(received_at) AS ts FROM execution_reports").fetchone()
        return row["ts"] if row and row["ts"] else None


def native_data_available() -> bool:
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM native_account_snapshots) +
                (SELECT COUNT(*) FROM native_mt5_accounts) +
                (SELECT COUNT(*) FROM native_mt5_events) AS count
            """
        ).fetchone()
        return bool(row and row["count"])


def native_assets(limit: int = 8) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT symbol, created_at FROM native_mt5_events WHERE symbol IS NOT NULL AND symbol != ''
            UNION ALL
            SELECT symbol, created_at FROM native_account_snapshots WHERE symbol IS NOT NULL AND symbol != ''
            UNION ALL
            SELECT symbol, created_at FROM native_mt5_accounts WHERE symbol IS NOT NULL AND symbol != ''
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall()
    for row in rows:
        symbol = str(row["symbol"] or "").strip()
        key = symbol.lower()
        if symbol and key not in seen:
            symbols.append(symbol)
            seen.add(key)
        if len(symbols) >= limit:
            break
    return symbols or DEFAULT_NATIVE_ASSETS


def save_native_heartbeat(heartbeat: NativeMT5Heartbeat) -> dict:
    payload = _safe_payload(heartbeat)
    heartbeat_at = first_present(heartbeat.time, now_iso())
    with db() as conn:
        control = _ensure_native_bot_control(
            conn,
            heartbeat.bot_id,
            heartbeat.symbol,
            heartbeat.magic_number,
            last_heartbeat_at=heartbeat_at,
            status=heartbeat.status,
            has_position=heartbeat.has_position,
            settings_summary=payload.get("settings_summary"),
        )
        _update_native_heartbeat(conn)
        return control


def get_native_control(bot_id: str, symbol: Optional[str] = None, magic_number: Optional[int] = None) -> dict:
    with db() as conn:
        control = _ensure_native_bot_control(conn, bot_id, symbol, magic_number)
        return {
            "bot_id": control.get("bot_id"),
            "enabled": bool(control.get("enabled", 1)),
            "pause_new_entries": not bool(control.get("enabled", 1)),
            "message": control.get("paused_reason") or "",
        }


def list_native_bot_controls(include_defaults: bool = True) -> list[dict]:
    with db() as conn:
        if include_defaults:
            for bot in DEFAULT_NATIVE_BOTS:
                _ensure_native_bot_control(conn, bot["bot_id"], bot["symbol"], bot["magic_number"])
        rows = conn.execute(
            """
            SELECT * FROM native_bot_controls
            ORDER BY
                CASE
                    WHEN bot_id LIKE 'NAS100%' THEN 1
                    WHEN bot_id LIKE 'SP500%' OR bot_id LIKE 'US500%' THEN 2
                    WHEN bot_id LIKE 'DJ30%' THEN 3
                    WHEN bot_id LIKE 'BTCUSD%' THEN 4
                    WHEN bot_id LIKE 'GER40%' THEN 5
                    ELSE 99
                END,
                symbol,
                bot_id
            """
        ).fetchall()
        controls = []
        active = {position_key(row): row for row in conn.execute("SELECT * FROM native_mt5_active_trades").fetchall()}
        for row in rows:
            item = dict(row)
            active_trade = _active_for_control(active, item)
            item["asset"] = _asset_from_bot(item.get("bot_id"), item.get("symbol"))
            item["display_name"] = _bot_display_name(item.get("bot_id"), item.get("symbol"))
            item["online_status"] = native_bot_online_status(item.get("last_heartbeat_at"))
            item["active_position"] = _position_from_row(active_trade) if active_trade else None
            controls.append(item)
        return controls


def get_native_bot_control(selector: str) -> Optional[dict]:
    selector = str(selector or "").strip()
    if not selector:
        return None
    controls = list_native_bot_controls(include_defaults=True)
    normalized = _normalize_selector(selector)
    for control in controls:
        keys = {
            _normalize_selector(control.get("bot_id")),
            _normalize_selector(control.get("symbol")),
            _normalize_selector(control.get("asset")),
        }
        if normalized in keys:
            return control
    return None


def native_config(bot_id: str) -> dict:
    control = get_native_bot_control(bot_id)
    if not control:
        with db() as conn:
            control = _ensure_native_bot_control(conn, bot_id, None, None)
        control = get_native_bot_control(bot_id) or control
    return {
        "bot_id": control.get("bot_id"),
        "enabled": bool(control.get("enabled", 1)),
        "symbol": _asset_from_bot(control.get("bot_id"), control.get("symbol")),
        "reason": control.get("paused_reason") or "",
    }


def set_native_bot_enabled(selector: str, enabled: bool, reason: str = "") -> Optional[dict]:
    control = get_native_bot_control(selector)
    if not control:
        return None
    with db() as conn:
        conn.execute(
            """
            UPDATE native_bot_controls
            SET enabled = ?, paused_reason = ?, updated_at = datetime('now')
            WHERE bot_id = ?
            """,
            (1 if enabled else 0, "" if enabled else reason, control["bot_id"]),
        )
    return get_native_bot_control(control["bot_id"])


def set_all_native_bots_enabled(enabled: bool, reason: str = "") -> list[dict]:
    controls = list_native_bot_controls(include_defaults=True)
    with db() as conn:
        conn.execute(
            """
            UPDATE native_bot_controls
            SET enabled = ?, paused_reason = ?, updated_at = datetime('now')
            """,
            (1 if enabled else 0, "" if enabled else reason),
        )
    return list_native_bot_controls(include_defaults=True)


def native_bot_online_status(last_heartbeat_at) -> str:
    parsed = _parse_datetime(last_heartbeat_at)
    if not parsed:
        return "offline"
    age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if age < 90:
        return "online"
    if age <= 300:
        return "stale"
    return "offline"


def performance_summary(period: str = "today", selector: Optional[str] = None) -> dict:
    start = _period_start(period)
    rows = _journal_rows(start, selector)
    event_rows = _trade_event_rows(start, selector)
    active_rows = _active_trade_rows(selector)
    grouped: dict[str, dict] = {}

    for row in rows:
        symbol = row.get("symbol") or "UNKNOWN"
        stats = grouped.setdefault(symbol, _empty_performance(symbol, row.get("bot_id"), row.get("magic_number")))
        if row.get("closed_at"):
            profit = float_or_zero(row.get("profit"))
            stats["trades_count"] += 1
            stats["closed_pnl"] += profit
            stats["gross_profit"] += profit if profit > 0 else 0.0
            stats["gross_loss"] += profit if profit < 0 else 0.0
            stats["wins"] += 1 if profit > 0 else 0
            stats["losses"] += 1 if profit < 0 else 0
            stats["breakeven"] += 1 if profit == 0 else 0
            stats["best_trade"] = _max_optional(stats["best_trade"], profit)
            stats["worst_trade"] = _min_optional(stats["worst_trade"], profit)
            stats["last_trade_time"] = _max_time(stats["last_trade_time"], row.get("closed_at"))
        stats["tp1_count"] += 1 if row.get("tp1_done") else 0
        stats["tp2_count"] += 1 if row.get("tp2_done") else 0
        stats["tp3_count"] += 1 if row.get("tp3_done") else 0
        stats["be_count"] += 1 if row.get("be_done") else 0

    for row in event_rows:
        symbol = row.get("symbol") or "UNKNOWN"
        stats = grouped.setdefault(symbol, _empty_performance(symbol, row.get("bot_id"), None))
        event_type = str(row.get("event_type") or "").lower()
        if event_type == "open_failed":
            stats["open_failed_count"] += 1
        elif event_type == "close_failed":
            stats["close_failed_count"] += 1

    for row in active_rows:
        symbol = row.get("symbol") or "UNKNOWN"
        stats = grouped.setdefault(symbol, _empty_performance(symbol, row.get("bot_id"), row.get("magic_number")))
        stats["floating_pnl"] += float_or_zero(row.get("profit"))

    items = []
    totals = _empty_performance("TOTAL", None, None)
    for stats in grouped.values():
        _finalize_performance(stats)
        items.append(stats)
        for key in ("trades_count", "wins", "losses", "breakeven", "tp1_count", "tp2_count", "tp3_count", "be_count", "open_failed_count", "close_failed_count"):
            totals[key] += stats[key]
        for key in ("closed_pnl", "floating_pnl", "gross_profit", "gross_loss", "total_pnl"):
            totals[key] += stats[key]
        totals["best_trade"] = _max_optional(totals["best_trade"], stats["best_trade"])
        totals["worst_trade"] = _min_optional(totals["worst_trade"], stats["worst_trade"])
    _finalize_performance(totals)
    return {
        "period": period,
        "start": start.isoformat() if start else None,
        "items": sorted(items, key=lambda item: item["symbol"]),
        "totals": totals,
    }


def journal_entries(period: str = "today", selector: Optional[str] = None, limit: int = 50) -> list[dict]:
    return _journal_rows(_period_start(period), selector, limit=limit)


def native_closed_trades(period: str = "today", selector: Optional[str] = None, limit: int = 50) -> list[dict]:
    start = _period_start(period)
    fetch_limit = min(limit * 10, 5000)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_mt5_closed_trades
            ORDER BY COALESCE(closed_at, created_at) DESC, created_at DESC
            LIMIT ?
            """,
            (fetch_limit,),
        ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        if not _row_in_period(data, start):
            continue
        if selector and not _selector_matches(data, selector):
            continue
        result.append(data)
        if len(result) >= limit:
            break
    return result


def native_journal_all(limit: int = 10000) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_trade_journal
            ORDER BY COALESCE(closed_at, opened_at, created_at) ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def first_native_account_snapshot_today() -> Optional[dict]:
    today_start = datetime.now(BERLIN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_account_snapshots
            ORDER BY COALESCE(snapshot_at, created_at) ASC, id ASC
            LIMIT 1000
            """
        ).fetchall()
    for row in rows:
        data = _native_account_from_row(row)
        parsed = _parse_datetime(first_present(data.get("snapshot_at"), data.get("created_at")))
        if parsed and parsed.astimezone(BERLIN_TZ) >= today_start:
            return data
    return None


def last_journal_trade(selector: Optional[str] = None) -> Optional[dict]:
    rows = _journal_rows(None, selector, limit=1, newest=True)
    return rows[0] if rows else None


def get_journal_trade(trade_id: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM native_trade_journal
            WHERE CAST(id AS TEXT) = ? OR trade_uid = ?
            ORDER BY id DESC LIMIT 1
            """,
            (str(trade_id), str(trade_id)),
        ).fetchone()
        return dict(row) if row else None


def save_native_screenshot_record(screenshot: NativeMT5Screenshot, file_path: str, caption: str) -> dict:
    payload = _safe_payload(screenshot)
    event_type = str(screenshot.event_type or "").strip().lower()
    with db() as conn:
        active = _find_native_active_trade(conn, screenshot)
        trade_uid = _trade_uid_from_event(screenshot, payload, active)
        cur = conn.execute(
            """
            INSERT INTO native_screenshots
                (trade_uid, bot_id, symbol, event_type, file_path, caption, time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_uid,
                screenshot.bot_id,
                screenshot.symbol,
                event_type,
                str(file_path),
                caption,
                screenshot.time,
            ),
        )
        screenshot_id = cur.lastrowid
        _ensure_native_bot_control(
            conn,
            screenshot.bot_id,
            screenshot.symbol,
            screenshot.magic_number,
            last_screenshot_at=first_present(screenshot.time, now_iso()),
        )
        if trade_uid:
            column = "open_screenshot_id" if event_type == "opened" else "close_screenshot_id" if event_type in NATIVE_EVENT_CLOSES_ACTIVE else None
            if column:
                conn.execute(
                    f"UPDATE native_trade_journal SET {column} = ?, updated_at = datetime('now') WHERE trade_uid = ?",
                    (screenshot_id, trade_uid),
                )
        row = conn.execute("SELECT * FROM native_screenshots WHERE id = ?", (screenshot_id,)).fetchone()
        return dict(row)


def last_native_screenshot(selector: Optional[str] = None) -> Optional[dict]:
    control = get_native_bot_control(selector) if selector else None
    filters = []
    params = []
    if control:
        filters.append("(bot_id = ? OR symbol = ?)")
        params.extend([control.get("bot_id"), control.get("symbol")])
    where = "WHERE " + " AND ".join(filters) if filters else ""
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM native_screenshots
            {where}
            ORDER BY COALESCE(time, created_at) DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row else None


def native_screenshots(selector: Optional[str] = None, limit: int = 20) -> list[dict]:
    control = get_native_bot_control(selector) if selector else None
    filters = []
    params = []
    if control:
        filters.append("(bot_id = ? OR symbol = ?)")
        params.extend([control.get("bot_id"), control.get("symbol")])
    where = "WHERE " + " AND ".join(filters) if filters else ""
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, trade_uid, bot_id, symbol, event_type, caption, time, created_at
            FROM native_screenshots
            {where}
            ORDER BY COALESCE(time, created_at) DESC, id DESC
            LIMIT ?
            """,
            [*params, max(1, min(int(limit or 20), 100))],
        ).fetchall()
        return [dict(row) for row in rows]


def native_screenshot_file(screenshot_id: int) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT id, file_path, caption, bot_id, symbol, event_type, time, created_at FROM native_screenshots WHERE id = ?",
            (screenshot_id,),
        ).fetchone()
        return dict(row) if row else None


def prune_native_screenshot_records(limit_per_bot: int = 20) -> list[str]:
    deleted_paths: list[str] = []
    with db() as conn:
        bot_ids = [
            row["bot_id"] or ""
            for row in conn.execute("SELECT DISTINCT COALESCE(bot_id, '') AS bot_id FROM native_screenshots").fetchall()
        ]
        for bot_id in bot_ids:
            rows = conn.execute(
                """
                SELECT id, file_path
                FROM native_screenshots
                WHERE COALESCE(bot_id, '') = ?
                ORDER BY COALESCE(time, created_at) DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (bot_id, limit_per_bot),
            ).fetchall()
            for row in rows:
                deleted_paths.append(row["file_path"])
                conn.execute("DELETE FROM native_screenshots WHERE id = ?", (row["id"],))
    return deleted_paths


def state_get(key: str) -> Optional[str]:
    with db() as conn:
        row = conn.execute("SELECT value FROM native_mt5_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def state_set(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO native_mt5_state (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """,
            (key, value),
        )


def mark_native_event_telegram_sent(dedupe_key: str | None, sent: bool) -> None:
    if not dedupe_key:
        return
    with db() as conn:
        conn.execute(
            "UPDATE native_trade_events SET telegram_sent = ? WHERE dedupe_key = ?",
            (1 if sent else 0, dedupe_key),
        )
        conn.execute(
            "UPDATE native_mt5_events SET telegram_sent = ? WHERE event_id = ?",
            (1 if sent else 0, dedupe_key),
        )


def _upsert_native_active_trade(conn, event: NativeMT5Event, payload: dict, force_new: bool) -> str:
    existing = None if force_new else _find_native_active_trade(conn, event)
    trade_key = existing["trade_key"] if existing else _new_trade_key(event)
    merged = _merge_native_trade(existing, event, payload)
    conn.execute(
        """
        INSERT INTO native_mt5_active_trades
            (trade_key, trade_uid, bot_id, symbol, magic_number, side, lot, entry, exit_price,
             current_price, sl, tp1, tp2, tp3, tp1_done, tp2_done, tp3_done,
             be_done, tp1_profit, tp2_profit, closed_percent, profit, balance, equity, status,
             last_event_type, opened_at, updated_at, message, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now'), ?, ?)
        ON CONFLICT(trade_key) DO UPDATE SET
            trade_uid = excluded.trade_uid,
            bot_id = excluded.bot_id,
            symbol = excluded.symbol,
            magic_number = excluded.magic_number,
            side = excluded.side,
            lot = excluded.lot,
            entry = excluded.entry,
            exit_price = excluded.exit_price,
            current_price = excluded.current_price,
            sl = excluded.sl,
            tp1 = excluded.tp1,
            tp2 = excluded.tp2,
            tp3 = excluded.tp3,
            tp1_done = excluded.tp1_done,
            tp2_done = excluded.tp2_done,
            tp3_done = excluded.tp3_done,
            be_done = excluded.be_done,
            tp1_profit = COALESCE(excluded.tp1_profit, tp1_profit),
            tp2_profit = COALESCE(excluded.tp2_profit, tp2_profit),
            closed_percent = excluded.closed_percent,
            profit = excluded.profit,
            balance = excluded.balance,
            equity = excluded.equity,
            status = excluded.status,
            last_event_type = excluded.last_event_type,
            opened_at = excluded.opened_at,
            updated_at = datetime('now'),
            message = excluded.message,
            payload = excluded.payload
        """,
        (
            trade_key,
            merged.get("trade_uid"),
            merged.get("bot_id"),
            merged.get("symbol"),
            merged.get("magic_number"),
            merged.get("side"),
            merged.get("lot"),
            merged.get("entry"),
            merged.get("exit_price"),
            merged.get("current_price"),
            merged.get("sl"),
            merged.get("tp1"),
            merged.get("tp2"),
            merged.get("tp3"),
            int(bool(merged.get("tp1_done"))),
            int(bool(merged.get("tp2_done"))),
            int(bool(merged.get("tp3_done"))),
            int(bool(merged.get("be_done"))),
            merged.get("tp1_profit"),
            merged.get("tp2_profit"),
            merged.get("closed_percent"),
            merged.get("profit"),
            merged.get("balance"),
            merged.get("equity"),
            merged.get("status"),
            merged.get("last_event_type"),
            merged.get("opened_at"),
            merged.get("message"),
            json.dumps(payload, ensure_ascii=False, default=str),
        ),
    )
    return trade_key


def _close_native_active_trade(conn, event: NativeMT5Event, payload: dict) -> None:
    existing = _find_native_active_trade(conn, event)
    if existing:
        trade_key = existing["trade_key"]
        merged = _merge_native_trade(existing, event, payload)
    else:
        trade_key = _new_trade_key(event)
        merged = _merge_native_trade(None, event, payload)
    closed_at = event.time or now_iso()
    conn.execute(
        """
        INSERT INTO native_mt5_closed_trades
            (trade_key, trade_uid, bot_id, symbol, magic_number, side, lot, entry, exit_price,
             sl, tp1, tp2, tp3, tp1_done, tp2_done, tp3_done, be_done,
             tp1_profit, tp2_profit, closed_percent, profit, balance, equity, status, opened_at,
             closed_at, message, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_key) DO UPDATE SET
            trade_uid = excluded.trade_uid,
            bot_id = excluded.bot_id,
            symbol = excluded.symbol,
            magic_number = excluded.magic_number,
            side = excluded.side,
            lot = excluded.lot,
            entry = excluded.entry,
            exit_price = excluded.exit_price,
            sl = excluded.sl,
            tp1 = excluded.tp1,
            tp2 = excluded.tp2,
            tp3 = excluded.tp3,
            tp1_done = excluded.tp1_done,
            tp2_done = excluded.tp2_done,
            tp3_done = excluded.tp3_done,
            be_done = excluded.be_done,
            tp1_profit = COALESCE(excluded.tp1_profit, tp1_profit),
            tp2_profit = COALESCE(excluded.tp2_profit, tp2_profit),
            closed_percent = excluded.closed_percent,
            profit = excluded.profit,
            balance = excluded.balance,
            equity = excluded.equity,
            status = excluded.status,
            opened_at = excluded.opened_at,
            closed_at = excluded.closed_at,
            message = excluded.message,
            payload = excluded.payload
        """,
        (
            trade_key,
            merged.get("trade_uid"),
            merged.get("bot_id"),
            merged.get("symbol"),
            merged.get("magic_number"),
            merged.get("side"),
            merged.get("lot"),
            merged.get("entry"),
            merged.get("exit_price"),
            merged.get("sl"),
            merged.get("tp1"),
            merged.get("tp2"),
            merged.get("tp3"),
            int(bool(merged.get("tp1_done"))),
            int(bool(merged.get("tp2_done"))),
            int(bool(merged.get("tp3_done"))),
            int(bool(merged.get("be_done"))),
            merged.get("tp1_profit"),
            merged.get("tp2_profit"),
            merged.get("closed_percent"),
            merged.get("profit"),
            merged.get("balance"),
            merged.get("equity"),
            merged.get("status"),
            merged.get("opened_at"),
            closed_at,
            merged.get("message"),
            json.dumps(payload, ensure_ascii=False, default=str),
        ),
    )
    conn.execute("DELETE FROM native_mt5_active_trades WHERE trade_key = ?", (trade_key,))


def _find_native_active_trade(conn, event: NativeMT5Event) -> Optional[dict]:
    filters = []
    params = []
    if event.bot_id:
        filters.append("bot_id = ?")
        params.append(event.bot_id)
    if event.symbol:
        filters.append("symbol = ?")
        params.append(event.symbol)
    if event.magic_number is not None:
        filters.append("magic_number = ?")
        params.append(event.magic_number)
    if not filters:
        return None
    query = "SELECT * FROM native_mt5_active_trades WHERE " + " AND ".join(filters)
    query += " ORDER BY updated_at DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def _ensure_native_bot_control(
    conn,
    bot_id: Optional[str],
    symbol: Optional[str],
    magic_number: Optional[int],
    *,
    last_heartbeat_at: Optional[str] = None,
    last_account_at: Optional[str] = None,
    last_event_at: Optional[str] = None,
    last_screenshot_at: Optional[str] = None,
    last_event_type: Optional[str] = None,
    status: Optional[str] = None,
    has_position: Optional[bool] = None,
    settings_summary=None,
) -> dict:
    bot_id = bot_id or _default_bot_id_for_symbol(symbol) or "unknown_native_bot"
    default = _default_bot(bot_id, symbol)
    symbol = first_present(symbol, default.get("symbol"))
    magic_number = first_present(magic_number, default.get("magic_number"))
    existing = conn.execute("SELECT * FROM native_bot_controls WHERE bot_id = ?", (bot_id,)).fetchone()
    settings_json = json.dumps(settings_summary, ensure_ascii=False, default=str) if settings_summary is not None else None
    if not existing:
        conn.execute(
            """
            INSERT INTO native_bot_controls
                (bot_id, symbol, magic_number, enabled, paused_reason, status, has_position,
                 last_event_type, settings_summary, last_heartbeat_at, last_account_at,
                 last_event_at, last_screenshot_at)
            VALUES (?, ?, ?, 1, '', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_id,
                symbol,
                magic_number,
                status,
                1 if has_position else 0,
                last_event_type,
                settings_json,
                last_heartbeat_at,
                last_account_at,
                last_event_at,
                last_screenshot_at,
            ),
        )
    else:
        updates = ["symbol = COALESCE(?, symbol)", "magic_number = COALESCE(?, magic_number)", "updated_at = datetime('now')"]
        params = [symbol, magic_number]
        for column, value in (
            ("status", status),
            ("has_position", 1 if has_position else 0 if has_position is not None else None),
            ("last_event_type", last_event_type),
            ("settings_summary", settings_json),
            ("last_heartbeat_at", last_heartbeat_at),
            ("last_account_at", last_account_at),
            ("last_event_at", last_event_at),
            ("last_screenshot_at", last_screenshot_at),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        params.append(bot_id)
        conn.execute(f"UPDATE native_bot_controls SET {', '.join(updates)} WHERE bot_id = ?", params)
    row = conn.execute("SELECT * FROM native_bot_controls WHERE bot_id = ?", (bot_id,)).fetchone()
    return dict(row) if row else {"bot_id": bot_id, "symbol": symbol, "magic_number": magic_number, "enabled": 1}


def _is_duplicate_native_event(conn, dedupe_key: str) -> bool:
    try:
        conn.execute("INSERT INTO native_event_dedupe (dedupe_key) VALUES (?)", (dedupe_key,))
        conn.execute("DELETE FROM native_event_dedupe WHERE created_at < datetime('now', '-2 days')")
        return False
    except sqlite3.IntegrityError:
        return True


def _native_event_dedupe_key(event: NativeMT5Event, payload: dict, event_type: str) -> str:
    explicit = first_present(payload.get("event_id"), payload.get("dedupe_key"))
    if explicit:
        return str(explicit)
    parsed = _parse_datetime(first_present(event.time, payload.get("time")))
    rounded_time = parsed.astimezone(timezone.utc).strftime("%Y%m%d%H%M") if parsed else str(first_present(event.time, payload.get("time"), ""))[:16]
    price = first_present(payload.get("close_price"), payload.get("price"), event.exit_price, event.current_price, event.entry, "")
    profit = first_present(payload.get("realized_net"), payload.get("total_net"), event.profit, payload.get("net_profit"), "")
    basis = "|".join(
        [
            str(event.bot_id or ""),
            str(event.symbol or ""),
            str(event.magic_number or ""),
            str(payload.get("trade_uid") or ""),
            str(payload.get("normalized_type") or event_type),
            str(first_present(payload.get("deal_ticket"), payload.get("ticket"), "")),
            str(first_present(payload.get("close_volume"), payload.get("closed_volume"), payload.get("volume"), "")),
            rounded_time,
            str(price),
            str(profit),
            str(payload.get("tp_index") or ""),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _record_native_trade_event(conn, event: NativeMT5Event, payload: dict, event_type: str, dedupe_key: str) -> str:
    existing = _find_native_active_trade(conn, event)
    if not existing and event_type in NATIVE_EVENT_CLOSES_ACTIVE:
        existing = _find_open_journal_trade(conn, event)
    trade_uid = _trade_uid_from_event(event, payload, existing)
    price = first_present(payload.get("price"), event.exit_price, event.current_price, event.entry)
    conn.execute(
        """
        INSERT OR IGNORE INTO native_trade_events
            (trade_uid, event_type, symbol, bot_id, side, price, profit, message, time, dedupe_key,
             event_id, normalized_type, should_notify, telegram_sent, payload, magic, position_id,
             order_ticket, deal_ticket, tp_index)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_uid,
            event_type,
            event.symbol,
            event.bot_id,
            event.side,
            price,
            event.profit,
            event.message,
            event.time,
            dedupe_key,
            payload.get("event_id"),
            payload.get("normalized_type"),
            1 if payload.get("should_notify") else 0,
            json.dumps(payload, ensure_ascii=False, default=str),
            event.magic_number,
            first_present(payload.get("position_id"), payload.get("position_ticket")),
            first_present(payload.get("order_ticket"), payload.get("order")),
            first_present(payload.get("deal_ticket"), payload.get("ticket")),
            payload.get("tp_index"),
        ),
    )
    _apply_journal_event(conn, event, payload, event_type, trade_uid)
    return trade_uid


def _find_open_journal_trade(conn, event: NativeMT5Event) -> Optional[dict]:
    filters = ["closed_at IS NULL", "status != 'closed'"]
    params = []
    if event.bot_id:
        filters.append("bot_id = ?")
        params.append(event.bot_id)
    if event.symbol:
        filters.append("symbol = ?")
        params.append(event.symbol)
    if event.magic_number is not None:
        filters.append("magic_number = ?")
        params.append(event.magic_number)
    if len(filters) <= 2:
        return None
    row = conn.execute(
        f"""
        SELECT * FROM native_trade_journal
        WHERE {" AND ".join(filters)}
        ORDER BY COALESCE(opened_at, created_at) DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return dict(row) if row else None


def _apply_journal_event(conn, event: NativeMT5Event, payload: dict, event_type: str, trade_uid: str) -> None:
    existing = conn.execute("SELECT * FROM native_trade_journal WHERE trade_uid = ?", (trade_uid,)).fetchone()
    realized_net = _payload_profit(payload) if any(payload.get(key) is not None for key in ("realized_net", "total_net", "profit_money", "profit", "commission", "swap")) else event.profit
    if event_type == "opened":
        conn.execute(
            """
            INSERT INTO native_trade_journal
                (trade_uid, bot_id, symbol, magic_number, side, lot, entry, sl, tp1, tp2, tp3,
                 opened_at, status, profit, balance_after, equity_after)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
            ON CONFLICT(trade_uid) DO UPDATE SET
                bot_id = excluded.bot_id,
                symbol = excluded.symbol,
                magic_number = excluded.magic_number,
                side = COALESCE(excluded.side, side),
                lot = COALESCE(excluded.lot, lot),
                entry = COALESCE(excluded.entry, entry),
                sl = COALESCE(excluded.sl, sl),
                tp1 = COALESCE(excluded.tp1, tp1),
                tp2 = COALESCE(excluded.tp2, tp2),
                tp3 = COALESCE(excluded.tp3, tp3),
                opened_at = COALESCE(excluded.opened_at, opened_at),
                status = 'open',
                updated_at = datetime('now')
            """,
            (
                trade_uid,
                event.bot_id,
                event.symbol,
                event.magic_number,
                event.side,
                event.lot,
                event.entry,
                event.sl,
                event.tp1,
                event.tp2,
                event.tp3,
                first_present(event.time, now_iso()),
                event.profit,
                event.balance,
                event.equity,
            ),
        )
        conn.execute(
            """
            UPDATE native_trade_journal
            SET position_id = COALESCE(?, position_id),
                order_ticket = COALESCE(?, order_ticket),
                lot_initial = COALESCE(?, lot_initial),
                entry_price = COALESCE(?, entry_price),
                sl_price = COALESCE(?, sl_price),
                tp1_price = COALESCE(?, tp1_price),
                tp2_price = COALESCE(?, tp2_price),
                tp3_price = COALESCE(?, tp3_price),
                updated_at = datetime('now')
            WHERE trade_uid = ?
            """,
            (
                first_present(payload.get("position_id"), payload.get("position_ticket")),
                first_present(payload.get("order_ticket"), payload.get("order")),
                first_present(payload.get("lot_initial"), event.lot),
                first_present(payload.get("entry_price"), event.entry),
                first_present(payload.get("sl_price"), event.sl),
                first_present(payload.get("tp1_price"), event.tp1),
                first_present(payload.get("tp2_price"), event.tp2),
                first_present(payload.get("tp3_price"), event.tp3),
                trade_uid,
            ),
        )
        return

    if not existing:
        conn.execute(
            """
            INSERT OR IGNORE INTO native_trade_journal
                (trade_uid, bot_id, symbol, magic_number, side, lot, entry, sl, tp1, tp2, tp3,
                 opened_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_uid,
                event.bot_id,
                event.symbol,
                event.magic_number,
                event.side,
                event.lot,
                event.entry,
                event.sl,
                event.tp1,
                event.tp2,
                event.tp3,
                first_present(event.time, now_iso()),
                event_type,
            ),
        )

    if event_type in NATIVE_EVENT_UPDATES_ACTIVE:
        flags = {
            "tp1_closed": "tp1_done = 1",
            "tp2_closed": "tp1_done = 1, tp2_done = 1",
            "tp3_closed": "tp1_done = 1, tp2_done = 1, tp3_done = 1",
            "be_moved": "be_done = 1",
        }[event_type]
        conn.execute(
            f"""
            UPDATE native_trade_journal
            SET {flags},
                status = ?,
                profit = COALESCE(?, profit),
                balance_after = COALESCE(?, balance_after),
                equity_after = COALESCE(?, equity_after),
                updated_at = datetime('now')
            WHERE trade_uid = ?
            """,
            (event_type, realized_net, event.balance, event.equity, trade_uid),
        )
        tp_index = {"tp1_closed": 1, "tp2_closed": 2, "tp3_closed": 3}.get(event_type)
        if tp_index:
            conn.execute(
                f"""
                UPDATE native_trade_journal
                SET tp{tp_index}_price = COALESCE(?, tp{tp_index}_price),
                    tp{tp_index}_volume = COALESCE(?, tp{tp_index}_volume),
                    tp{tp_index}_percent = COALESCE(?, tp{tp_index}_percent),
                    tp{tp_index}_profit = COALESCE(?, tp{tp_index}_profit),
                    tp{tp_index}_commission = COALESCE(?, tp{tp_index}_commission),
                    tp{tp_index}_swap = COALESCE(?, tp{tp_index}_swap),
                    tp{tp_index}_net = COALESCE(?, tp{tp_index}_net),
                    updated_at = datetime('now')
                WHERE trade_uid = ?
                """,
                (
                    first_present(payload.get(f"tp{tp_index}_price"), payload.get(f"tp{tp_index}"), event.exit_price, event.current_price),
                    first_present(payload.get(f"tp{tp_index}_volume"), payload.get("close_volume"), payload.get("closed_volume"), payload.get("volume")),
                    first_present(payload.get(f"tp{tp_index}_percent"), payload.get("closed_percent")),
                    first_present(payload.get(f"tp{tp_index}_profit"), payload.get("profit")),
                    first_present(payload.get(f"tp{tp_index}_commission"), payload.get("commission")),
                    first_present(payload.get(f"tp{tp_index}_swap"), payload.get("swap")),
                    first_present(payload.get(f"tp{tp_index}_net"), realized_net),
                    trade_uid,
                ),
            )
    elif event_type in NATIVE_EVENT_CLOSES_ACTIVE:
        opened_at = existing["opened_at"] if existing and "opened_at" in existing.keys() else None
        closed_at = first_present(event.time, now_iso())
        duration_seconds = None
        opened_dt = _parse_datetime(opened_at)
        closed_dt = _parse_datetime(closed_at)
        if opened_dt and closed_dt:
            duration_seconds = max(0, int((closed_dt.astimezone(timezone.utc) - opened_dt.astimezone(timezone.utc)).total_seconds()))
        conn.execute(
            """
            UPDATE native_trade_journal
            SET status = 'closed',
                close_reason = ?,
                closed_at = ?,
                profit = ?,
                exit_price = COALESCE(?, exit_price),
                total_profit = COALESCE(?, total_profit),
                total_commission = COALESCE(?, total_commission),
                total_swap = COALESCE(?, total_swap),
                total_net = COALESCE(?, total_net),
                deal_ticket = COALESCE(?, deal_ticket),
                duration_seconds = COALESCE(?, duration_seconds),
                balance_after = COALESCE(?, balance_after),
                equity_after = COALESCE(?, equity_after),
                updated_at = datetime('now')
            WHERE trade_uid = ?
            """,
            (
                first_present(payload.get("close_reason"), payload.get("reason"), event_type),
                closed_at,
                realized_net,
                first_present(payload.get("close_price"), event.exit_price, event.current_price),
                first_present(payload.get("total_profit"), payload.get("profit")),
                payload.get("commission"),
                payload.get("swap"),
                first_present(payload.get("total_net"), payload.get("realized_net"), realized_net),
                first_present(payload.get("deal_ticket"), payload.get("ticket")),
                duration_seconds,
                event.balance,
                event.equity,
                trade_uid,
            ),
        )


def _upsert_native_journal_error(conn, event: NativeMT5Event, payload: dict, event_type: str) -> None:
    trade_uid = _trade_uid_from_event(event, payload, _find_native_active_trade(conn, event))
    conn.execute(
        """
        INSERT OR IGNORE INTO native_trade_journal
            (trade_uid, bot_id, symbol, magic_number, side, lot, entry, opened_at, status, close_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_uid,
            event.bot_id,
            event.symbol,
            event.magic_number,
            event.side,
            event.lot,
            event.entry,
            first_present(event.time, now_iso()),
            event_type,
            event.message,
        ),
    )


def _update_native_heartbeat(conn) -> None:
    conn.execute(
        """
        INSERT INTO native_mt5_state (key, value, updated_at)
        VALUES ('last_native_heartbeat_at', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (now_iso(),),
    )


def _payload_profit(payload: dict) -> float:
    value = first_present(payload.get("realized_net"), payload.get("total_net"), payload.get("profit_money"), payload.get("net_profit"), payload.get("pnl"))
    if value is None and payload.get("profit") is not None:
        try:
            return float(payload.get("profit") or 0.0) + float(payload.get("commission") or 0.0) + float(payload.get("swap") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_today_berlin(value) -> bool:
    parsed = _parse_datetime(value)
    if not parsed:
        return False
    return parsed.astimezone(BERLIN_TZ).date() == datetime.now(BERLIN_TZ).date()


def _parse_datetime(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
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


def _trade_uid_from_event(event, payload: dict, existing: Optional[dict] = None) -> str:
    existing = existing or {}
    explicit = first_present(payload.get("trade_uid"), getattr(event, "trade_uid", None), existing.get("trade_uid"))
    if explicit:
        return str(explicit)
    position_id = first_present(payload.get("position_id"), payload.get("position_ticket"), payload.get("position"))
    if position_id:
        return "|".join(
            [
                str(first_present(getattr(event, "bot_id", None), existing.get("bot_id"), "native")),
                str(first_present(getattr(event, "symbol", None), existing.get("symbol"), "unknown_symbol")),
                str(first_present(getattr(event, "magic_number", None), existing.get("magic_number"), payload.get("magic"), "")),
                str(position_id),
            ]
        )
    ticket = first_present(payload.get("ticket"), getattr(event, "ticket", None))
    if ticket:
        return f"{first_present(getattr(event, 'bot_id', None), existing.get('bot_id'), 'native')}_{ticket}"
    basis = "|".join(
        [
            str(first_present(getattr(event, "bot_id", None), existing.get("bot_id"), "unknown_bot")),
            str(first_present(getattr(event, "symbol", None), existing.get("symbol"), "unknown_symbol")),
            str(first_present(getattr(event, "magic_number", None), existing.get("magic_number"), "")),
            str(first_present(payload.get("open_time"), existing.get("opened_at"), getattr(event, "time", None), payload.get("time"), now_iso())),
            str(first_present(getattr(event, "side", None), existing.get("side"), payload.get("side"), "")),
        ]
    )
    return "trade_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def _default_bot(bot_id: Optional[str], symbol: Optional[str] = None) -> dict:
    normalized_bot = _normalize_selector(bot_id)
    normalized_symbol = _normalize_selector(symbol)
    aliases = {
        "US500": "SP500",
        "US500R": "SP500",
        "GER40FT": "GER40",
    }
    normalized_bot = aliases.get(normalized_bot, normalized_bot)
    normalized_symbol = aliases.get(normalized_symbol, normalized_symbol)
    for bot in DEFAULT_NATIVE_BOTS:
        if normalized_bot in {_normalize_selector(bot["bot_id"]), _normalize_selector(bot["asset"])}:
            return bot
        if normalized_symbol and normalized_symbol in {_normalize_selector(bot["symbol"]), _normalize_selector(bot["asset"])}:
            return bot
    return {}


def _default_bot_id_for_symbol(symbol: Optional[str]) -> Optional[str]:
    bot = _default_bot(None, symbol)
    return bot.get("bot_id") if bot else None


def _normalize_selector(value) -> str:
    return re_safe(str(value or "")).upper().replace(".R", "").replace(".", "")


def re_safe(value: str) -> str:
    return "".join(char for char in value if char.isalnum() or char in ("_", "."))


def _asset_from_bot(bot_id: Optional[str], symbol: Optional[str]) -> str:
    bot = _default_bot(bot_id, symbol)
    if bot:
        return bot["asset"]
    text = str(bot_id or symbol or "UNKNOWN")
    asset = text.split("_", 1)[0].replace(".r", "").replace(".R", "").upper()
    return {"US500": "SP500", "GER40FT": "GER40"}.get(asset, asset)


def _bot_display_name(bot_id: Optional[str], symbol: Optional[str] = None) -> str:
    asset = _asset_from_bot(bot_id, symbol)
    if "ORB" in str(bot_id or "") or asset in {"NAS100", "SP500", "DJ30", "BTCUSD", "GER40"}:
        return f"{asset} ORB/VWAP"
    return str(bot_id or symbol or "native bot")


def position_key(row) -> tuple:
    return (row["bot_id"], row["symbol"], row["magic_number"])


def _active_for_control(active: dict, control: dict):
    key = (control.get("bot_id"), control.get("symbol"), control.get("magic_number"))
    if key in active:
        return active[key]
    for row in active.values():
        if control.get("bot_id") and row["bot_id"] == control.get("bot_id"):
            return row
        if control.get("symbol") and row["symbol"] == control.get("symbol"):
            return row
    return None


def _position_from_row(row) -> dict:
    return {
        "trade_uid": row["trade_uid"] or row["trade_key"],
        "symbol": row["symbol"],
        "side": row["side"],
        "lot": row["lot"],
        "profit": row["profit"],
        "opened_at": row["opened_at"],
    }


def _period_start(period: str) -> Optional[datetime]:
    normalized = str(period or "today").strip().lower()
    now = datetime.now(BERLIN_TZ)
    if normalized in ("today", "day", "1d"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized in ("7d", "7", "week"):
        return now - timedelta(days=7)
    if normalized in ("30d", "30", "month"):
        return now - timedelta(days=30)
    if normalized in ("all", "alltime"):
        return None
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _row_in_period(row: dict, start: Optional[datetime]) -> bool:
    if start is None:
        return True
    parsed = _parse_datetime(first_present(row.get("closed_at"), row.get("opened_at"), row.get("time"), row.get("created_at")))
    if not parsed:
        return False
    return parsed.astimezone(BERLIN_TZ) >= start


def _selector_matches(row: dict, selector: Optional[str]) -> bool:
    if not selector:
        return True
    control = get_native_bot_control(selector)
    normalized = _normalize_selector(selector)
    keys = {_normalize_selector(row.get("bot_id")), _normalize_selector(row.get("symbol"))}
    if control:
        keys.update({_normalize_selector(control.get("bot_id")), _normalize_selector(control.get("symbol")), _normalize_selector(control.get("asset"))})
    return normalized in keys


def _journal_rows(start: Optional[datetime], selector: Optional[str] = None, limit: int = 500, newest: bool = False) -> list[dict]:
    order = "DESC" if newest else "ASC"
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM native_trade_journal
            ORDER BY COALESCE(closed_at, opened_at, created_at) {order}, id {order}
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows if _row_in_period(dict(row), start) and _selector_matches(dict(row), selector)]


def _trade_event_rows(start: Optional[datetime], selector: Optional[str] = None, limit: int = 5000) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM native_trade_events
            ORDER BY COALESCE(time, created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows if _row_in_period(dict(row), start) and _selector_matches(dict(row), selector)]


def _period_window(period: str) -> tuple[Optional[datetime], Optional[datetime]]:
    normalized = str(period or "today").strip().lower()
    now = datetime.now(BERLIN_TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized in ("today", "day", "1d"):
        return today, today + timedelta(days=1)
    if normalized == "yesterday":
        start = today - timedelta(days=1)
        return start, today
    if normalized in ("week", "7d", "7"):
        return now - timedelta(days=7), None
    if normalized in ("all", "alltime"):
        return None, None
    return today, today + timedelta(days=1)


def _row_in_named_period(row: dict, period: str) -> bool:
    start, end = _period_window(period)
    if start is None and end is None:
        return True
    parsed = _parse_datetime(first_present(row.get("close_time"), row.get("closed_at"), row.get("open_time"), row.get("opened_at"), row.get("time"), row.get("created_at")))
    if not parsed:
        return False
    local = parsed.astimezone(BERLIN_TZ)
    if start and local < start:
        return False
    if end and local >= end:
        return False
    return True


def _trade_duration_minutes(open_time, close_time) -> Optional[int]:
    opened = _parse_datetime(open_time)
    closed = _parse_datetime(close_time)
    if not opened or not closed:
        return None
    return max(0, int((closed.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() // 60))


def _format_minute_time(value) -> Optional[str]:
    parsed = _parse_datetime(value)
    if not parsed:
        return value
    return parsed.astimezone(BERLIN_TZ).strftime("%Y-%m-%d %H:%M")


def _estimate_r_multiple_value(profit, entry, sl, lot) -> Optional[float]:
    profit_value = float_or_zero(profit)
    entry_value = float_or_zero(entry)
    sl_value = float_or_zero(sl)
    lots_value = abs(float_or_zero(lot) or 1.0)
    if not entry_value or not sl_value or entry_value == sl_value:
        return None
    risk = abs(entry_value - sl_value) * lots_value
    if risk <= 0:
        return None
    return round(profit_value / risk, 2)


def _normalize_trade_row(row: dict) -> dict:
    profit = first_present(row.get("total_profit"), row.get("profit"), row.get("profit_money"))
    close_time = first_present(row.get("close_time"), row.get("closed_at"))
    open_time = first_present(row.get("open_time"), row.get("opened_at"))
    exit_price = first_present(row.get("exit_price"), row.get("close_price"))
    status = str(row.get("status") or "").strip().lower()
    is_open = status == "open" or not close_time or exit_price is None
    if is_open:
        status = "open"
        profit_value = None
        r_multiple = None
    else:
        if not status or status == "closed":
            if float_or_zero(profit) > 0:
                status = "win"
            elif float_or_zero(profit) < 0:
                status = "loss"
            else:
                status = "breakeven"
        profit_value = float_or_zero(profit)
        r_multiple = _estimate_r_multiple_value(
            profit,
            first_present(row.get("entry_price"), row.get("entry")),
            first_present(row.get("sl_price"), row.get("sl")),
            first_present(row.get("lots"), row.get("lot")),
        )
    return {
        "id": row.get("id"),
        "ticket": row.get("ticket"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "lots": first_present(row.get("lots"), row.get("lot")),
        "lot": first_present(row.get("lot"), row.get("lots")),
        "entry_price": first_present(row.get("entry_price"), row.get("entry")),
        "entry": first_present(row.get("entry"), row.get("entry_price")),
        "sl_price": first_present(row.get("sl_price"), row.get("sl")),
        "sl": first_present(row.get("sl"), row.get("sl_price")),
        "exit_price": exit_price,
        "total_profit": profit_value,
        "profit": profit_value,
        "r_multiple": r_multiple,
        "status": status,
        "open_time": open_time,
        "opened_at": open_time,
        "close_time": close_time,
        "closed_at": close_time,
        "duration_minutes": _trade_duration_minutes(open_time, close_time),
        "tp1_hit": bool(row.get("tp1_hit") or row.get("tp1_done")),
        "tp2_hit": bool(row.get("tp2_hit") or row.get("tp2_done")),
        "bot_id": row.get("bot_id"),
    }


def get_all_trades(period: str = "today", limit: int = 50, bot_id: Optional[str] = None) -> list[dict]:
    selector = None if bot_id in (None, "", "all", "ALL") else bot_id
    with db() as conn:
        journal = conn.execute(
            """
            SELECT * FROM native_trade_journal
            ORDER BY
                CASE WHEN closed_at IS NULL OR closed_at = '' THEN 0 ELSE 1 END,
                COALESCE(closed_at, opened_at, created_at) DESC,
                id DESC
            """
        ).fetchall()
        events = conn.execute(
            """
            SELECT * FROM native_trade_events
            ORDER BY COALESCE(time, created_at) DESC, id DESC
            LIMIT 5000
            """
        ).fetchall()
    result: list[dict] = []
    seen_uids: set[str] = set()
    for raw in journal:
        row = dict(raw)
        if not _row_in_named_period(row, period) or not _selector_matches(row, selector):
            continue
        uid = str(row.get("trade_uid") or "")
        if uid:
            seen_uids.add(uid)
        result.append(_normalize_trade_row(row))
    for raw in events:
        row = dict(raw)
        uid = str(row.get("trade_uid") or "")
        if uid and uid in seen_uids:
            continue
        if str(row.get("event_type") or "") not in {"position_closed", "closed_by_signal", "tp1_closed", "tp2_closed", "tp3_closed"}:
            continue
        if not _row_in_named_period(row, period) or not _selector_matches(row, selector):
            continue
        result.append(_normalize_trade_row({
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "bot_id": row.get("bot_id"),
            "profit": row.get("profit"),
            "closed_at": row.get("time"),
            "status": "win" if float_or_zero(row.get("profit")) > 0 else "loss" if float_or_zero(row.get("profit")) < 0 else "breakeven",
        }))
    def sort_key(row: dict):
        parsed = _parse_datetime(first_present(row.get("close_time"), row.get("open_time")))
        stamp = parsed.timestamp() if parsed else 0
        return (0 if not row.get("close_time") else 1, -stamp)

    result.sort(key=sort_key)
    return result[: max(0, int(limit or 50))]


def _asset_matches(row: dict, asset: str = "ALL") -> bool:
    normalized = str(asset or "ALL").strip().upper()
    if normalized in ("", "ALL", "ВСЕ"):
        return True
    return normalized in str(row.get("symbol") or row.get("bot_id") or "").upper()


def _normalize_filtered_journal_trade(row: dict) -> dict:
    data = _normalize_trade_row(row)
    data["id"] = row.get("id")
    data["source"] = row.get("source") or ("manual" if not row.get("bot_id") else "bot")
    data["profit_money"] = data.get("profit")
    data["duration_minutes"] = data.get("duration_minutes")
    return data


def _normalize_filtered_backtest_trade(row: dict) -> dict:
    profit = row.get("profit_money")
    status = str(row.get("status") or "").strip().lower()
    is_open = status == "open" or not row.get("close_time") or row.get("exit_price") is None
    if is_open:
        status = "open"
        profit_value = None
        r_multiple = None
    elif not status:
        if float_or_zero(profit) > 0:
            status = "win"
        elif float_or_zero(profit) < 0:
            status = "loss"
        else:
            status = "breakeven"
        profit_value = float_or_zero(profit)
        r_multiple = first_present(row.get("r_multiple"), _estimate_r_multiple_value(profit, row.get("entry_price"), row.get("sl_price"), row.get("lots")))
    else:
        profit_value = float_or_zero(profit)
        r_multiple = first_present(row.get("r_multiple"), _estimate_r_multiple_value(profit, row.get("entry_price"), row.get("sl_price"), row.get("lots")))
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "lots": row.get("lots"),
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "profit_money": profit_value,
        "profit": profit_value,
        "r_multiple": r_multiple,
        "status": status,
        "open_time": _format_minute_time(row.get("open_time")),
        "opened_at": row.get("open_time"),
        "close_time": _format_minute_time(row.get("close_time")),
        "closed_at": row.get("close_time"),
        "duration_minutes": _trade_duration_minutes(row.get("open_time"), row.get("close_time")),
        "tp1_hit": bool(row.get("tp1_hit")),
        "tp2_hit": bool(row.get("tp2_hit")),
        "source": row.get("source") or "backtest",
        "bot_id": row.get("bot_id"),
    }


def get_trades_filtered(source: str = "all", period: str = "today", asset: str = "ALL", limit: int = 50, offset: int = 0) -> list[dict]:
    normalized_source = str(source or "all").strip().lower()
    normalized_period = {"day": "today", "month": "30d"}.get(str(period or "").strip().lower(), period)
    trades: list[dict] = []
    with db() as conn:
        if normalized_source in ("bot", "manual", "all"):
            rows = conn.execute(
                """
                SELECT * FROM native_trade_journal
                ORDER BY
                    CASE WHEN closed_at IS NULL OR closed_at = '' THEN 0 ELSE 1 END,
                    COALESCE(closed_at, opened_at, created_at) DESC,
                    id DESC
                """
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                row_source = row.get("source") or ("manual" if not row.get("bot_id") else "bot")
                if normalized_source == "manual" and not (row_source == "manual" or row.get("bot_id") is None):
                    continue
                if normalized_source == "bot" and row_source == "manual":
                    continue
                if not _row_in_named_period(row, normalized_period) or not _asset_matches(row, asset):
                    continue
                trades.append(_normalize_filtered_journal_trade(row))
        if normalized_source in ("backtest", "all"):
            rows = conn.execute(
                """
                SELECT * FROM backtest_trades
                ORDER BY open_time DESC, id DESC
                """
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                if not _row_in_named_period(row, normalized_period) or not _asset_matches(row, asset):
                    continue
                trades.append(_normalize_filtered_backtest_trade(row))

    def sort_key(row: dict):
        parsed = _parse_datetime(first_present(row.get("close_time"), row.get("open_time")))
        stamp = parsed.timestamp() if parsed else 0
        return (0 if not row.get("close_time") else 1, -stamp)

    trades.sort(key=sort_key)
    start = max(0, int(offset or 0))
    end = start + max(0, int(limit or 50))
    return trades[start:end]


def get_stats_filtered(source: str = "bot", period: str = "week", asset: str = "ALL") -> dict:
    normalized_period = {"day": "today", "month": "30d"}.get(str(period or "").strip().lower(), period)
    rows = get_trades_filtered(source=source, period=normalized_period, asset=asset, limit=10000, offset=0)
    closed_statuses = {"win", "loss", "be", "breakeven", "closed", "position_closed", "closed_by_signal"}
    closed_rows = [
        row for row in rows
        if str(row.get("status") or "").strip().lower() in closed_statuses
        and str(row.get("status") or "").strip().lower() != "open"
    ]
    profits = [float_or_zero(row.get("profit_money")) for row in closed_rows if row.get("profit_money") is not None]
    r_values = [float_or_zero(row.get("r_multiple")) for row in closed_rows if row.get("r_multiple") is not None]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    total = len(closed_rows)
    gross_profit = round(sum(wins), 2)
    gross_loss = round(sum(losses), 2)
    best = max(profits) if profits else None
    worst = min(profits) if profits else None
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / total) * 100, 1) if total else 0,
        "total_pnl": round(sum(profits), 2),
        "best_trade": round(best, 2) if best is not None else None,
        "worst_trade": round(worst, 2) if worst is not None else None,
        "avg_trade": round(sum(profits) / total, 2) if total else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else 0,
        "avg_r": round(sum(r_values) / len(r_values), 2) if r_values else None,
        "max_r": round(max(r_values), 2) if r_values else None,
        "tp1_hit_rate": round((sum(1 for row in closed_rows if row.get("tp1_hit")) / total) * 100, 1) if total else 0,
        "tp2_hit_rate": round((sum(1 for row in closed_rows if row.get("tp2_hit")) / total) * 100, 1) if total else 0,
        "source": source,
        "period": period,
        "asset": asset,
    }


def save_history_deals(bot_id: str, deals: list[dict]) -> int:
    saved = 0
    with db() as conn:
        for deal in deals or []:
            if not isinstance(deal, dict):
                continue
            resolved_bot = first_present(bot_id, deal.get("bot_id"))
            ticket = first_present(deal.get("deal_ticket"), deal.get("ticket"))
            symbol = deal.get("symbol")
            close_time = first_present(deal.get("deal_time"), deal.get("close_time"), deal.get("closed_at"))
            if not symbol or ticket is None or ticket == "":
                continue
            position_id = first_present(deal.get("position_id"), deal.get("position_ticket"))
            order_ticket = deal.get("order_ticket")
            magic_number = first_present(deal.get("magic_number"), deal.get("magic"))
            volume = first_present(deal.get("volume"), deal.get("lots"), deal.get("lot"))
            price = first_present(deal.get("price"), deal.get("exit_price"), deal.get("close_price"))
            profit = float_or_zero(deal.get("profit"))
            commission = float_or_zero(deal.get("commission"))
            swap = float_or_zero(deal.get("swap"))
            net = first_present(deal.get("net"), deal.get("total_net"), deal.get("net_profit"), deal.get("total_profit"))
            net_value = float_or_zero(net if net is not None else profit + commission + swap)
            source = deal.get("source") or "mt5_history"
            cur = conn.execute(
                """
                INSERT INTO history_deals
                    (deal_ticket, order_ticket, position_id, symbol, magic_number, bot_id, side,
                     entry_type, deal_type, volume, price, profit, commission, swap, net,
                     deal_time, comment, source, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deal_ticket) DO UPDATE SET
                    order_ticket = excluded.order_ticket,
                    position_id = excluded.position_id,
                    symbol = excluded.symbol,
                    magic_number = excluded.magic_number,
                    bot_id = excluded.bot_id,
                    side = excluded.side,
                    entry_type = excluded.entry_type,
                    deal_type = excluded.deal_type,
                    volume = excluded.volume,
                    price = excluded.price,
                    profit = excluded.profit,
                    commission = excluded.commission,
                    swap = excluded.swap,
                    net = excluded.net,
                    deal_time = excluded.deal_time,
                    comment = excluded.comment,
                    source = excluded.source,
                    payload = excluded.payload,
                    updated_at = datetime('now')
                """,
                (
                    str(ticket),
                    str(order_ticket) if order_ticket is not None else None,
                    str(position_id) if position_id is not None else None,
                    symbol,
                    magic_number,
                    resolved_bot,
                    deal.get("side"),
                    deal.get("entry_type"),
                    deal.get("deal_type"),
                    volume,
                    price,
                    profit,
                    commission,
                    swap,
                    net_value,
                    close_time,
                    deal.get("comment"),
                    source,
                    json.dumps(deal, ensure_ascii=False, default=str),
                ),
            )
            saved += 1 if cur.rowcount else 0

            # Journal is position-centric. History sync may send only closing deals,
            # so create or update one durable journal row per MT5 position_id.
            trade_uid = str(first_present(
                deal.get("trade_uid"),
                f"{resolved_bot}_{symbol}_{magic_number}_{position_id}" if position_id else None,
                f"history_{ticket}",
            ))
            grouped = conn.execute(
                """
                SELECT
                    bot_id, symbol, magic_number, position_id,
                    MIN(deal_time) AS first_time,
                    MAX(deal_time) AS last_time,
                    SUM(volume) AS total_volume,
                    MAX(price) AS last_price,
                    SUM(profit) AS total_profit,
                    SUM(commission) AS total_commission,
                    SUM(swap) AS total_swap,
                    SUM(net) AS total_net
                FROM history_deals
                WHERE COALESCE(bot_id, '') = COALESCE(?, '')
                  AND symbol = ?
                  AND COALESCE(magic_number, '') = COALESCE(?, '')
                  AND COALESCE(position_id, deal_ticket) = COALESCE(?, ?)
                GROUP BY bot_id, symbol, magic_number, position_id
                """,
                (resolved_bot, symbol, magic_number, str(position_id) if position_id is not None else None, str(ticket)),
            ).fetchone()
            if grouped:
                total_net = float_or_zero(grouped["total_net"])
                status = "win" if total_net > 0 else "loss" if total_net < 0 else "breakeven"
                conn.execute(
                    """
                    INSERT INTO native_trade_journal
                        (trade_uid, ticket, bot_id, symbol, magic_number, side, lot, exit_price,
                         opened_at, closed_at, status, close_reason, profit, commission, swap,
                         total_profit, total_commission, total_swap, total_net, position_id,
                         deal_ticket, comment, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_uid) DO UPDATE SET
                        ticket = COALESCE(excluded.ticket, ticket),
                        bot_id = excluded.bot_id,
                        symbol = excluded.symbol,
                        magic_number = excluded.magic_number,
                        side = COALESCE(excluded.side, side),
                        lot = excluded.lot,
                        exit_price = excluded.exit_price,
                        opened_at = COALESCE(opened_at, excluded.opened_at),
                        closed_at = excluded.closed_at,
                        status = excluded.status,
                        close_reason = COALESCE(close_reason, excluded.close_reason),
                        profit = excluded.profit,
                        commission = excluded.commission,
                        swap = excluded.swap,
                        total_profit = excluded.total_profit,
                        total_commission = excluded.total_commission,
                        total_swap = excluded.total_swap,
                        total_net = excluded.total_net,
                        position_id = COALESCE(excluded.position_id, position_id),
                        deal_ticket = excluded.deal_ticket,
                        comment = COALESCE(excluded.comment, comment),
                        source = excluded.source,
                        updated_at = datetime('now')
                    """,
                    (
                        trade_uid,
                        str(ticket),
                        grouped["bot_id"],
                        grouped["symbol"],
                        grouped["magic_number"],
                        deal.get("side"),
                        grouped["total_volume"],
                        grouped["last_price"],
                        grouped["first_time"],
                        grouped["last_time"],
                        status,
                        deal.get("close_reason") or "history_sync",
                        total_net,
                        grouped["total_commission"],
                        grouped["total_swap"],
                        grouped["total_profit"],
                        grouped["total_commission"],
                        grouped["total_swap"],
                        total_net,
                        grouped["position_id"],
                        str(ticket),
                        deal.get("comment"),
                        source,
                    ),
                )
            continue
            trade_uid = str(first_present(
                deal.get("trade_uid"),
                f"history_{ticket}" if ticket is not None and ticket != "" else None,
                "history_" + hashlib.sha1("|".join([
                    str(resolved_bot or ""),
                    str(symbol or ""),
                    str(close_time or ""),
                    str(deal.get("side") or ""),
                ]).encode("utf-8")).hexdigest()[:24],
            ))
            total_profit = first_present(deal.get("total_profit"), deal.get("net_profit"), deal.get("profit"))
            status = str(deal.get("status") or "").strip().lower()
            if not status:
                if not close_time or deal.get("exit_price") is None:
                    status = "open"
                elif float_or_zero(total_profit) > 0:
                    status = "win"
                elif float_or_zero(total_profit) < 0:
                    status = "loss"
                else:
                    status = "breakeven"
            params = (
                trade_uid,
                ticket,
                resolved_bot,
                symbol,
                first_present(deal.get("magic_number"), deal.get("magic")),
                deal.get("side"),
                first_present(deal.get("lots"), deal.get("lot")),
                first_present(deal.get("entry_price"), deal.get("entry")),
                deal.get("exit_price"),
                open_time,
                close_time,
                status,
                deal.get("close_reason"),
                total_profit,
                deal.get("commission"),
                deal.get("swap"),
                deal.get("comment"),
                deal.get("source") or "bot",
            )
            if ticket is not None and ticket != "":
                cur = conn.execute(
                    """
                    INSERT INTO native_trade_journal
                        (trade_uid, ticket, bot_id, symbol, magic_number, side, lot, entry, exit_price,
                         opened_at, closed_at, status, close_reason, profit, commission, swap, comment, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticket) DO UPDATE SET
                        bot_id = excluded.bot_id,
                        symbol = excluded.symbol,
                        magic_number = excluded.magic_number,
                        side = excluded.side,
                        lot = excluded.lot,
                        entry = excluded.entry,
                        exit_price = excluded.exit_price,
                        opened_at = excluded.opened_at,
                        closed_at = excluded.closed_at,
                        status = excluded.status,
                        close_reason = excluded.close_reason,
                        profit = excluded.profit,
                        commission = excluded.commission,
                        swap = excluded.swap,
                        comment = excluded.comment,
                        source = excluded.source,
                        updated_at = datetime('now')
                    """,
                    params,
                )
                saved += 1 if cur.rowcount else 0
                continue

            existing = conn.execute(
                """
                SELECT id FROM native_trade_journal
                WHERE COALESCE(bot_id, '') = COALESCE(?, '')
                  AND symbol = ?
                  AND opened_at = ?
                ORDER BY id DESC LIMIT 1
                """,
                (resolved_bot, symbol, open_time),
            ).fetchone()
            if existing:
                cur = conn.execute(
                    """
                    UPDATE native_trade_journal
                    SET exit_price = ?, closed_at = ?, status = ?, close_reason = ?,
                        profit = ?, commission = ?, swap = ?, comment = ?, source = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (deal.get("exit_price"), close_time, status, deal.get("close_reason"), total_profit,
                     deal.get("commission"), deal.get("swap"), deal.get("comment"), deal.get("source") or "bot", existing["id"]),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO native_trade_journal
                        (trade_uid, ticket, bot_id, symbol, magic_number, side, lot, entry, exit_price,
                         opened_at, closed_at, status, close_reason, profit, commission, swap, comment, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
            saved += 1 if cur.rowcount else 0
    return saved


def save_backtest_trades(bot_id: str, trades_list: list[dict]) -> int:
    print(f"native-backtest received bot_id={bot_id} trades={len(trades_list or [])}")
    saved = 0
    with db() as conn:
        for trade in trades_list or []:
            if not isinstance(trade, dict):
                continue
            resolved_bot = first_present(bot_id, trade.get("bot_id"))
            symbol = trade.get("symbol")
            open_time = first_present(trade.get("open_time"), trade.get("opened_at"))
            if not resolved_bot or not symbol or not open_time:
                continue
            cur = conn.execute(
                """
                INSERT INTO backtest_trades
                    (bot_id, symbol, side, lots, entry_price, sl_price, tp1_price, tp2_price, tp3_price,
                     open_time, close_time, exit_price, profit_money, r_multiple, status, tp1_hit, tp2_hit, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, symbol, open_time) DO UPDATE SET
                    side = excluded.side,
                    lots = excluded.lots,
                    entry_price = excluded.entry_price,
                    sl_price = excluded.sl_price,
                    tp1_price = excluded.tp1_price,
                    tp2_price = excluded.tp2_price,
                    tp3_price = excluded.tp3_price,
                    close_time = excluded.close_time,
                    exit_price = excluded.exit_price,
                    profit_money = excluded.profit_money,
                    r_multiple = excluded.r_multiple,
                    status = excluded.status,
                    tp1_hit = excluded.tp1_hit,
                    tp2_hit = excluded.tp2_hit,
                    source = excluded.source
                """,
                (
                    resolved_bot,
                    symbol,
                    trade.get("side"),
                    trade.get("lots"),
                    first_present(trade.get("entry_price"), trade.get("entry")),
                    first_present(trade.get("sl_price"), trade.get("sl")),
                    first_present(trade.get("tp1_price"), trade.get("tp1")),
                    first_present(trade.get("tp2_price"), trade.get("tp2")),
                    first_present(trade.get("tp3_price"), trade.get("tp3")),
                    open_time,
                    first_present(trade.get("close_time"), trade.get("closed_at")),
                    trade.get("exit_price"),
                    first_present(trade.get("profit_money"), trade.get("total_profit"), trade.get("profit")),
                    first_present(trade.get("r_multiple"), trade.get("profit_r")),
                    trade.get("status"),
                    1 if trade.get("tp1_hit") else 0,
                    1 if trade.get("tp2_hit") else 0,
                    trade.get("source") or "backtest",
                ),
            )
            if cur.rowcount:
                saved += 1
    print(f"native-backtest saved bot_id={bot_id} saved={saved}")
    return saved


def backtest_trades(bot_id: Optional[str] = None, limit: int = 500) -> list[dict]:
    selector = None if bot_id in (None, "", "all", "ALL") else bot_id
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM backtest_trades
            ORDER BY open_time DESC, id DESC
            LIMIT ?
            """,
            (max(1, int(limit or 500)),),
        ).fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        if selector and not _selector_matches(row, selector):
            continue
        result.append(row)
    return result


def native_trade_events(limit: int = 100, selector: Optional[str] = None) -> list[dict]:
    return _trade_event_rows(None, selector, limit=max(1, min(int(limit or 100), 500)))


def backtest_summary(bot_id: Optional[str] = None) -> dict:
    rows = [_normalize_filtered_backtest_trade(row) for row in backtest_trades(bot_id=bot_id, limit=10000)]
    closed_rows = [row for row in rows if str(row.get("status") or "").lower() != "open"]
    profits = [float_or_zero(row.get("profit_money")) for row in closed_rows if row.get("profit_money") is not None]
    r_values = [float_or_zero(row.get("r_multiple")) for row in closed_rows if row.get("r_multiple") is not None]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    total = len(closed_rows)
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return {
        "total": total,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / total) * 100, 1) if total else 0,
        "total_pnl": round(sum(profits), 2),
        "best_trade": round(max(profits), 2) if profits else None,
        "worst_trade": round(min(profits), 2) if profits else None,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else 0,
        "avg_r": round(sum(r_values) / len(r_values), 2) if r_values else None,
        "tp1_hit_rate": round((sum(1 for row in closed_rows if row.get("tp1_hit")) / total) * 100, 1) if total else 0,
        "tp2_hit_rate": round((sum(1 for row in closed_rows if row.get("tp2_hit")) / total) * 100, 1) if total else 0,
    }


def _active_trade_rows(selector: Optional[str] = None) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM native_mt5_active_trades").fetchall()
    return [dict(row) for row in rows if _selector_matches(dict(row), selector)]


def _empty_performance(symbol: str, bot_id: Optional[str], magic_number: Optional[int]) -> dict:
    return {
        "symbol": symbol,
        "bot_id": bot_id,
        "magic_number": magic_number,
        "trades_count": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "winrate": 0.0,
        "closed_pnl": 0.0,
        "floating_pnl": 0.0,
        "total_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
        "avg_win": None,
        "avg_loss": None,
        "best_trade": None,
        "worst_trade": None,
        "tp1_count": 0,
        "tp2_count": 0,
        "tp3_count": 0,
        "be_count": 0,
        "open_failed_count": 0,
        "close_failed_count": 0,
        "last_trade_time": None,
        "status": "⚪ мало данных",
        "realized_available": True,
    }


def _finalize_performance(stats: dict) -> None:
    trades = stats["trades_count"]
    stats["closed_pnl"] = round(stats["closed_pnl"], 2)
    stats["floating_pnl"] = round(stats["floating_pnl"], 2)
    stats["total_pnl"] = round(stats["closed_pnl"] + stats["floating_pnl"], 2)
    stats["gross_profit"] = round(stats["gross_profit"], 2)
    stats["gross_loss"] = round(stats["gross_loss"], 2)
    stats["winrate"] = round((stats["wins"] / trades) * 100, 1) if trades else 0.0
    stats["profit_factor"] = round(stats["gross_profit"] / abs(stats["gross_loss"]), 2) if stats["gross_loss"] < 0 else (None if stats["gross_profit"] == 0 else 999.0)
    stats["avg_win"] = round(stats["gross_profit"] / stats["wins"], 2) if stats["wins"] else None
    stats["avg_loss"] = round(stats["gross_loss"] / stats["losses"], 2) if stats["losses"] else None
    if stats["open_failed_count"] or stats["close_failed_count"]:
        stats["status"] = "есть ошибки исполнения"
    elif trades < 3:
        stats["status"] = "⚪ мало данных"
    elif stats["closed_pnl"] < 0 and trades >= 5:
        stats["status"] = "⚠️ слабый актив"
    elif stats["closed_pnl"] > 0 and (stats["profit_factor"] or 0) >= 1.2:
        stats["status"] = "рабочий"
    else:
        stats["status"] = "нейтрально"


def _max_optional(current, value):
    if value is None:
        return current
    return value if current is None or value > current else current


def _min_optional(current, value):
    if value is None:
        return current
    return value if current is None or value < current else current


def _max_time(current, value):
    if not current:
        return value
    current_dt = _parse_datetime(current)
    value_dt = _parse_datetime(value)
    if not value_dt:
        return current
    if not current_dt or value_dt > current_dt:
        return value
    return current


def _merge_profit(event_type: str, new_profit, existing_profit) -> Optional[float]:
    """Accumulate profit for TP partial closes and final closes instead of overwriting."""
    if event_type in NATIVE_TP_EVENT_TYPES or event_type in NATIVE_REALIZED_EVENT_TYPES:
        if new_profit is None:
            return existing_profit
        try:
            return float(existing_profit or 0) + float(new_profit)
        except (TypeError, ValueError):
            return new_profit
    return first_present(new_profit, existing_profit)


def _merge_native_trade(existing: Optional[dict], event: NativeMT5Event, payload: dict) -> dict:
    existing = existing or {}
    event_type = str(first_present(payload.get("event_type"), event.event_type) or "").strip().lower()
    realized_net = _payload_profit(payload) if any(payload.get(key) is not None for key in ("realized_net", "total_net", "profit_money", "profit", "commission", "swap")) else event.profit
    entry = first_present(event.entry, existing.get("entry"))
    sl = first_present(event.sl, existing.get("sl"))
    if event_type == "be_moved" and event.sl is None:
        sl = entry
    tp1_done = bool(existing.get("tp1_done")) or event_type in {"tp1_closed", "tp2_closed", "tp3_closed"}
    tp2_done = bool(existing.get("tp2_done")) or event_type in {"tp2_closed", "tp3_closed"}
    tp3_done = bool(existing.get("tp3_done")) or event_type == "tp3_closed"
    be_done = bool(existing.get("be_done")) or event_type == "be_moved"
    # Capture individual TP profits at the moment each TP fires (event.profit = this TP's slice)
    tp1_profit = existing.get("tp1_profit")
    tp2_profit = existing.get("tp2_profit")
    if event_type == "tp1_closed" and realized_net is not None:
        tp1_profit = realized_net
    elif event_type == "tp2_closed" and realized_net is not None:
        tp2_profit = realized_net
    return {
        "trade_uid": _trade_uid_from_event(event, payload, existing),
        "bot_id": first_present(event.bot_id, existing.get("bot_id")),
        "symbol": first_present(event.symbol, existing.get("symbol")),
        "magic_number": first_present(event.magic_number, existing.get("magic_number")),
        "side": first_present(event.side, existing.get("side")),
        "lot": first_present(event.lot, existing.get("lot")),
        "entry": entry,
        "exit_price": first_present(event.exit_price, event.current_price, existing.get("exit_price")),
        "current_price": first_present(event.current_price, existing.get("current_price")),
        "sl": sl,
        "tp1": first_present(event.tp1, existing.get("tp1")),
        "tp2": first_present(event.tp2, existing.get("tp2")),
        "tp3": first_present(event.tp3, existing.get("tp3")),
        "tp1_done": tp1_done,
        "tp2_done": tp2_done,
        "tp3_done": tp3_done,
        "be_done": be_done,
        "tp1_profit": tp1_profit,
        "tp2_profit": tp2_profit,
        "closed_percent": first_present(event.closed_percent, existing.get("closed_percent")),
        "profit": _merge_profit(event_type, realized_net, existing.get("profit")),
        "balance": first_present(event.balance, existing.get("balance")),
        "equity": first_present(event.equity, existing.get("equity")),
        "status": event_type,
        "last_event_type": event_type,
        "opened_at": first_present(existing.get("opened_at"), event.time, now_iso()),
        "message": first_present(event.message, existing.get("message")),
        "payload": payload,
    }


def get_trade_for_notification(event: NativeMT5Event) -> Optional[dict]:
    """Return active or just-closed trade row for enriching Telegram notifications."""
    event_type = str(event.event_type or "").strip().lower()
    payload = _safe_payload(event)
    trade_uid = first_present(payload.get("trade_uid"), getattr(event, "trade_uid", None))
    filters, params = [], []
    if trade_uid:
        with db() as conn:
            table = "native_mt5_closed_trades" if event_type in NATIVE_REALIZED_EVENT_TYPES else "native_mt5_active_trades"
            row = conn.execute(f"SELECT * FROM {table} WHERE trade_uid = ? ORDER BY updated_at DESC LIMIT 1", (trade_uid,)).fetchone()
            if row:
                return dict(row)
            row = conn.execute("SELECT * FROM native_trade_journal WHERE trade_uid = ? ORDER BY updated_at DESC LIMIT 1", (trade_uid,)).fetchone()
            if row:
                return dict(row)
    if event.bot_id:
        filters.append("bot_id = ?")
        params.append(event.bot_id)
    if event.symbol:
        filters.append("symbol = ?")
        params.append(event.symbol)
    if event.magic_number is not None:
        filters.append("magic_number = ?")
        params.append(event.magic_number)
    if not filters:
        return None
    where = " AND ".join(filters)
    with db() as conn:
        if event_type in NATIVE_REALIZED_EVENT_TYPES:
            row = conn.execute(
                f"SELECT * FROM native_mt5_closed_trades WHERE {where} ORDER BY COALESCE(closed_at, created_at) DESC LIMIT 1",
                params,
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT * FROM native_mt5_active_trades WHERE {where} ORDER BY updated_at DESC LIMIT 1",
                params,
            ).fetchone()
    return dict(row) if row else None


def get_native_trade_journal(trade_uid: str | None) -> Optional[dict]:
    if not trade_uid:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM native_trade_journal WHERE trade_uid = ? ORDER BY updated_at DESC LIMIT 1", (trade_uid,)).fetchone()
        return dict(row) if row else None


def _safe_payload(model) -> dict:
    return model.model_dump(mode="json", exclude={"secret"})


def _decode_payload(value) -> dict:
    try:
        payload = json.loads(value or "{}")
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


def _new_trade_key(event: NativeMT5Event) -> str:
    basis = "|".join(
        [
            event.bot_id or "unknown_bot",
            event.symbol or "unknown_symbol",
            str(event.magic_number or ""),
            event.side or "",
            event.time or now_iso(),
        ]
    )
    return "native_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def _synthetic_ticket(trade_key) -> int:
    digest = hashlib.sha1(str(trade_key or "").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % 2147483647


def first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def float_or_zero(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
