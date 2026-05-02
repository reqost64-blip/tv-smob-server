from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .database import db


APPROVAL_TTL_MINUTES = 15


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def parse_value(value: Optional[str]) -> Any:
    if value is None:
        return None
    normalized = value.strip()
    lower_value = normalized.lower()
    if lower_value == "true":
        return True
    if lower_value == "false":
        return False
    try:
        if "." in normalized:
            return float(normalized)
        return int(normalized)
    except ValueError:
        return value


def get_setting(key: str, default: Any = None) -> Any:
    with db() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        return parse_value(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (key, _serialize(value)),
        )


def list_settings() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM bot_settings ORDER BY key").fetchall()
        return {
            row["key"]: {"value": parse_value(row["value"]), "updated_at": row["updated_at"]}
            for row in rows
        }


def create_pending_approval(
    chat_id: str,
    command_text: str,
    parsed_action: dict,
    old_value: Any,
    new_value: Any,
) -> dict:
    approval_id = uuid.uuid4().hex[:10]
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MINUTES)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO pending_approvals
                (approval_id, chat_id, command_text, parsed_action, old_value, new_value, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                chat_id,
                command_text,
                json.dumps(parsed_action, default=str),
                _serialize(old_value) if old_value is not None else None,
                _serialize(new_value),
                expires_at.replace(microsecond=0).isoformat(),
            ),
        )
    return get_pending_approval(approval_id) or {}


def get_pending_approval(approval_id: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM pending_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return dict(row) if row else None


def list_pending_approvals(chat_id: Optional[str] = None) -> list[dict]:
    query = "SELECT * FROM pending_approvals WHERE status = 'pending'"
    params: tuple = ()
    if chat_id:
        query += " AND chat_id = ?"
        params = (chat_id,)
    query += " ORDER BY created_at DESC"
    with db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def approve_pending_approval(approval_id: str, actor: str) -> tuple[bool, str, Optional[dict]]:
    approval = get_pending_approval(approval_id)
    if not approval:
        return False, "Approval not found.", None
    if approval["status"] != "pending":
        return False, f"Approval already {approval['status']}.", approval
    if _is_expired(approval["expires_at"]):
        _mark_approval(approval_id, "expired")
        return False, "Approval expired.", approval

    parsed_action = json.loads(approval["parsed_action"])
    setting_key = parsed_action["setting_key"]
    set_setting(setting_key, parse_value(approval["new_value"]))
    _mark_approval(approval_id, "approved")
    record_audit_event(
        "approval_applied",
        actor,
        approval["command_text"],
        approval["old_value"],
        approval["new_value"],
    )
    return True, "Applied.", get_pending_approval(approval_id)


def reject_pending_approval(approval_id: str, actor: str) -> tuple[bool, str, Optional[dict]]:
    approval = get_pending_approval(approval_id)
    if not approval:
        return False, "Approval not found.", None
    if approval["status"] != "pending":
        return False, f"Approval already {approval['status']}.", approval
    _mark_approval(approval_id, "rejected")
    record_audit_event(
        "approval_rejected",
        actor,
        approval["command_text"],
        approval["old_value"],
        approval["new_value"],
    )
    return True, "Rejected.", get_pending_approval(approval_id)


def record_audit_event(
    event_type: str,
    actor: str,
    command_text: Optional[str] = None,
    before_value: Any = None,
    after_value: Any = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (event_type, actor, command_text, before_value, after_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                actor,
                command_text,
                _serialize(before_value) if before_value is not None else None,
                _serialize(after_value) if after_value is not None else None,
            ),
        )


def audit_log(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (min(max(limit, 1), 500),),
        ).fetchall()
        return [dict(row) for row in rows]


def _mark_approval(approval_id: str, status: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE pending_approvals SET status = ? WHERE approval_id = ?",
            (status, approval_id),
        )


def _is_expired(expires_at: str) -> bool:
    try:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expiry
    except ValueError:
        return False
