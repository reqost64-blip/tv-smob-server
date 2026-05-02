from __future__ import annotations

from typing import Optional

from .database import db
from .models import AccountSnapshot, DealReport, PositionsSnapshot


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


def latest_account_snapshot() -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def current_positions() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM positions_snapshots ORDER BY symbol, ticket").fetchall()
        return [dict(row) for row in rows]


def trades_today() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM deal_reports
            WHERE date(COALESCE(closed_at, created_at)) = date('now')
            ORDER BY COALESCE(closed_at, created_at) DESC, id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


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
    account = latest_account_snapshot()
    if account:
        return account.get("created_at")
    with db() as conn:
        row = conn.execute("SELECT MAX(received_at) AS ts FROM execution_reports").fetchone()
        return row["ts"] if row and row["ts"] else None
