from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from . import config
from .database import db
from .models import AccountSnapshot, DealReport, NativeMT5AccountSnapshot, NativeMT5Event, PositionsSnapshot


NATIVE_EVENT_UPDATES_ACTIVE = {"tp1_closed", "tp2_closed", "tp3_closed", "be_moved"}
NATIVE_EVENT_CLOSES_ACTIVE = {"position_closed", "closed_by_signal"}


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


def save_native_event(event: NativeMT5Event) -> None:
    payload = _safe_payload(event)
    event_type = str(event.event_type or "").strip().lower()
    payload["event_type"] = event_type
    payload.setdefault("source", "mt5_native")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO native_mt5_events
                (event_type, source, bot_id, symbol, magic_number, event_time, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                payload.get("source"),
                event.bot_id,
                event.symbol,
                event.magic_number,
                event.time,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        if event_type == "opened":
            _upsert_native_active_trade(conn, event, payload, force_new=False)
        elif event_type in NATIVE_EVENT_UPDATES_ACTIVE:
            _upsert_native_active_trade(conn, event, payload, force_new=False)
        elif event_type in NATIVE_EVENT_CLOSES_ACTIVE:
            _close_native_active_trade(conn, event, payload)


def latest_account_snapshot() -> Optional[dict]:
    if config.is_native_mt5_only():
        return latest_native_account_snapshot()
    with db() as conn:
        row = conn.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def latest_native_account_snapshot() -> Optional[dict]:
    with db() as conn:
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
                        "currency": payload.get("currency") or "USD",
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
        data = dict(row)
        payload = _decode_payload(data.get("payload"))
        return {
            "balance": data.get("balance"),
            "equity": data.get("equity"),
            "margin": data.get("margin"),
            "free_margin": data.get("free_margin"),
            "margin_level": payload.get("margin_level"),
            "currency": payload.get("currency") or "USD",
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
                    "symbol": data.get("symbol"),
                    "side": data.get("side"),
                    "lot": data.get("lot"),
                    "entry_price": data.get("entry"),
                    "current_price": first_present(data.get("current_price"), data.get("exit_price")),
                    "sl": data.get("sl"),
                    "tp": first_present(data.get("tp3"), data.get("tp2"), data.get("tp1")),
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
            WHERE date(COALESCE(closed_at, created_at)) = date('now')
            ORDER BY COALESCE(closed_at, created_at) DESC
            """
        ).fetchall()
        trades = []
        for row in rows:
            data = dict(row)
            ticket = _synthetic_ticket(data.get("trade_key"))
            trades.append(
                {
                    "deal_ticket": ticket,
                    "position_ticket": ticket,
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
                    "created_at": data.get("created_at"),
                    "source": "mt5_native",
                }
            )
        return trades


def pnl_today() -> dict:
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


def last_mt5_heartbeat() -> Optional[str]:
    if config.is_native_mt5_only():
        with db() as conn:
            row = conn.execute(
                """
                SELECT MAX(ts) AS ts
                FROM (
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
                (SELECT COUNT(*) FROM native_mt5_accounts) +
                (SELECT COUNT(*) FROM native_mt5_events) AS count
            """
        ).fetchone()
        return bool(row and row["count"])


def _upsert_native_active_trade(conn, event: NativeMT5Event, payload: dict, force_new: bool) -> str:
    existing = None if force_new else _find_native_active_trade(conn, event)
    trade_key = existing["trade_key"] if existing else _new_trade_key(event)
    merged = _merge_native_trade(existing, event, payload)
    conn.execute(
        """
        INSERT INTO native_mt5_active_trades
            (trade_key, bot_id, symbol, magic_number, side, lot, entry, exit_price,
             current_price, sl, tp1, tp2, tp3, closed_percent, profit, balance,
             equity, status, last_event_type, opened_at, updated_at, message, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now'), ?, ?)
        ON CONFLICT(trade_key) DO UPDATE SET
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
            (trade_key, bot_id, symbol, magic_number, side, lot, entry, exit_price,
             sl, tp1, tp2, tp3, closed_percent, profit, balance, equity, status,
             opened_at, closed_at, message, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_key) DO UPDATE SET
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


def _merge_native_trade(existing: Optional[dict], event: NativeMT5Event, payload: dict) -> dict:
    existing = existing or {}
    event_type = str(event.event_type or "").strip().lower()
    entry = first_present(event.entry, existing.get("entry"))
    sl = first_present(event.sl, existing.get("sl"))
    if event_type == "be_moved" and event.sl is None:
        sl = entry
    return {
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
        "closed_percent": first_present(event.closed_percent, existing.get("closed_percent")),
        "profit": first_present(event.profit, existing.get("profit")),
        "balance": first_present(event.balance, existing.get("balance")),
        "equity": first_present(event.equity, existing.get("equity")),
        "status": event_type,
        "last_event_type": event_type,
        "opened_at": first_present(existing.get("opened_at"), event.time, now_iso()),
        "message": first_present(event.message, existing.get("message")),
        "payload": payload,
    }


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
