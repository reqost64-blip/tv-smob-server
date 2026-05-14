from __future__ import annotations

import json
from typing import Optional

from .database import db


def save_bias_report(report: dict) -> dict:
    payload = json.dumps(report, ensure_ascii=False, default=str)
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO bias_reports
                (report_date, run_at, ny_time, berlin_time, macro_risk, data_quality_score, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.get("report_date"),
                report.get("run_at"),
                report.get("ny_time"),
                report.get("berlin_time"),
                report.get("macro_risk") or "UNKNOWN",
                report.get("data_quality_score"),
                payload,
            ),
        )
        report["id"] = cur.lastrowid
    return report


def latest_bias_report() -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM bias_reports
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    payload.setdefault("id", row["id"])
    payload.setdefault("report_date", row["report_date"])
    payload.setdefault("run_at", row["run_at"])
    payload.setdefault("ny_time", row["ny_time"])
    payload.setdefault("berlin_time", row["berlin_time"])
    payload.setdefault("macro_risk", row["macro_risk"])
    payload.setdefault("data_quality_score", row["data_quality_score"])
    payload.setdefault("created_at", row["created_at"])
    return payload

