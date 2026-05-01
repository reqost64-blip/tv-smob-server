import json
from typing import Optional
from .database import db
from .models import WebhookPayload, ExecutionReport


def signal_exists(signal_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM commands WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        return row is not None


def enqueue(payload: WebhookPayload) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO commands (signal_id, payload, status) VALUES (?, ?, 'queued')",
            (payload.signal_id, payload.model_dump_json()),
        )


def fetch_next_queued() -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM commands WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE commands SET status = 'sent', updated_at = datetime('now') WHERE signal_id = ?",
            (row["signal_id"],),
        )
        return dict(row)


def acknowledge(signal_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE commands SET status = 'acknowledged', updated_at = datetime('now') "
            "WHERE signal_id = ? AND status = 'sent'",
            (signal_id,),
        )
        return cur.rowcount > 0


def save_execution_report(report: ExecutionReport) -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO execution_reports
               (signal_id, ticket, status, message, executed_price, executed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report.signal_id,
                report.ticket,
                report.status,
                report.message,
                report.executed_price,
                report.executed_at,
            ),
        )


def record_event(event_type: str, signal_id: Optional[str] = None, payload: Optional[dict] = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO bot_events (event_type, signal_id, payload) VALUES (?, ?, ?)",
            (event_type, signal_id, json.dumps(payload or {}, default=str)),
        )


def command_counts() -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM commands GROUP BY status"
        ).fetchall()
        counts = {"queued": 0, "sent": 0, "acknowledged": 0}
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts


def last_signal_id() -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT signal_id FROM commands ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["signal_id"] if row else None


def last_execution_report() -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM execution_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def today_summary() -> dict:
    with db() as conn:
        signals = conn.execute(
            "SELECT COUNT(*) AS count FROM commands WHERE date(created_at) = date('now')"
        ).fetchone()["count"]
        opened = conn.execute(
            "SELECT COUNT(*) AS count FROM execution_reports "
            "WHERE lower(status) = 'opened' AND date(received_at) = date('now')"
        ).fetchone()["count"]
        rejected = conn.execute(
            "SELECT COUNT(*) AS count FROM bot_events "
            "WHERE event_type = 'rejected_signal' AND date(created_at) = date('now')"
        ).fetchone()["count"]
        return {
            "signals": signals,
            "opened": opened,
            "rejected": rejected,
            "estimated_pnl": None,
        }
