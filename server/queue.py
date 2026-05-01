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
