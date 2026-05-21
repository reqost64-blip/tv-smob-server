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


def save_live_bias_report(report: dict, sent_to_telegram: bool = False, send_reason: str | None = None) -> dict:
    timestamp = str(report.get("timestamp") or report.get("run_at") or "")
    sent_flag = 1 if sent_to_telegram else 0
    with db() as conn:
        for row in report.get("symbols") or []:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            payload = json.dumps(row, ensure_ascii=False, default=str)
            factor_scores = json.dumps(row.get("factor_scores") or {}, ensure_ascii=False, default=str)
            source_availability = json.dumps(row.get("source_availability") or {}, ensure_ascii=False, default=str)
            reasons = json.dumps(row.get("reasons") or [], ensure_ascii=False, default=str)
            invalidation = json.dumps(row.get("invalidation") or row.get("invalidation_info") or {}, ensure_ascii=False, default=str)
            risk_flags = json.dumps(row.get("risk_flags") or row.get("flags") or [], ensure_ascii=False, default=str)
            values = (
                symbol,
                timestamp,
                row.get("direction"),
                int(row.get("confidence") or 0),
                int(row.get("long_probability") or row.get("long_percent") or 0),
                int(row.get("short_probability") or row.get("short_percent") or 0),
                float(row.get("final_score") if row.get("final_score") is not None else row.get("score") or 0),
                row.get("strength") or "WEAK",
                row.get("risk") or "UNKNOWN",
                float(row.get("data_quality_score") or 0),
                factor_scores,
                source_availability,
                reasons,
                invalidation,
                risk_flags,
                sent_flag,
                send_reason,
                payload,
            )
            conn.execute(
                """
                INSERT INTO bias_snapshots_history
                    (symbol, timestamp, direction, confidence, long_probability, short_probability, final_score,
                     strength, risk, data_quality_score, factor_scores, source_availability, reasons,
                     invalidation_info, risk_flags, sent_to_telegram, send_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.execute(
                """
                INSERT INTO latest_live_bias
                    (symbol, timestamp, direction, confidence, long_probability, short_probability, final_score,
                     strength, risk, data_quality_score, factor_scores, source_availability, reasons,
                     invalidation_info, risk_flags, sent_to_telegram, send_reason, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(symbol) DO UPDATE SET
                    timestamp=excluded.timestamp,
                    direction=excluded.direction,
                    confidence=excluded.confidence,
                    long_probability=excluded.long_probability,
                    short_probability=excluded.short_probability,
                    final_score=excluded.final_score,
                    strength=excluded.strength,
                    risk=excluded.risk,
                    data_quality_score=excluded.data_quality_score,
                    factor_scores=excluded.factor_scores,
                    source_availability=excluded.source_availability,
                    reasons=excluded.reasons,
                    invalidation_info=excluded.invalidation_info,
                    risk_flags=excluded.risk_flags,
                    sent_to_telegram=excluded.sent_to_telegram,
                    send_reason=excluded.send_reason,
                    payload=excluded.payload,
                    updated_at=datetime('now')
                """,
                values,
            )
    return report


def latest_live_bias(symbol: str | None = None) -> list[dict]:
    params: tuple = ()
    where = ""
    if symbol:
        where = "WHERE symbol = ?"
        params = (symbol.upper(),)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM latest_live_bias
            {where}
            ORDER BY symbol ASC
            """,
            params,
        ).fetchall()
    return [_live_bias_row_to_payload(row) for row in rows]


def live_bias_history(symbol: str | None = None, limit: int = 100) -> list[dict]:
    max_rows = max(1, min(int(limit or 100), 1000))
    params: tuple
    where = ""
    if symbol:
        where = "WHERE symbol = ?"
        params = (symbol.upper(), max_rows)
    else:
        params = (max_rows,)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM bias_snapshots_history
            {where}
            ORDER BY timestamp DESC, created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_live_bias_row_to_payload(row) for row in rows]


def _live_bias_row_to_payload(row) -> dict:
    data = dict(row)
    try:
        payload = json.loads(data.get("payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    payload.setdefault("symbol", data.get("symbol"))
    payload.setdefault("timestamp", data.get("timestamp"))
    payload.setdefault("direction", data.get("direction"))
    payload.setdefault("confidence", data.get("confidence"))
    payload.setdefault("long_probability", data.get("long_probability"))
    payload.setdefault("short_probability", data.get("short_probability"))
    payload.setdefault("final_score", data.get("final_score"))
    payload.setdefault("strength", data.get("strength"))
    payload.setdefault("risk", data.get("risk"))
    payload.setdefault("data_quality_score", data.get("data_quality_score"))
    payload.setdefault("sent_to_telegram", bool(data.get("sent_to_telegram")))
    payload.setdefault("send_reason", data.get("send_reason"))
    payload.setdefault("created_at", data.get("created_at") or data.get("updated_at"))
    for key, column in (
        ("factor_scores", "factor_scores"),
        ("source_availability", "source_availability"),
        ("reasons", "reasons"),
        ("invalidation", "invalidation_info"),
        ("risk_flags", "risk_flags"),
    ):
        if key not in payload:
            try:
                payload[key] = json.loads(data.get(column) or "[]" if key in {"reasons", "risk_flags"} else data.get(column) or "{}")
            except (TypeError, json.JSONDecodeError):
                payload[key] = [] if key in {"reasons", "risk_flags"} else {}
    return payload

