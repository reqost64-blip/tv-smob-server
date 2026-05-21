from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from .database import db


PUBLIC_SIGNAL_FIELDS = {
    "signal_id", "timestamp", "source_type", "source_name", "symbol", "direction", "setup",
    "entry", "entry_zone_low", "entry_zone_high", "sl", "tp1", "tp2", "tp3", "timeframe",
    "parse_status", "expiry_minutes", "status", "verdict", "score", "confidence",
    "risk_level", "reasons", "rejection_reasons", "telegram_sent", "send_reason",
    "dedupe_key", "created_at", "updated_at",
}


def save_signal(signal: dict, telegram_sent: bool = False, send_reason: str | None = None) -> dict:
    payload = json.dumps(signal, ensure_ascii=False, default=str)
    reasons = json.dumps(signal.get("reasons") or [], ensure_ascii=False, default=str)
    rejection_reasons = json.dumps(signal.get("rejection_reasons") or [], ensure_ascii=False, default=str)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO signal_events
                (signal_id, timestamp, source_type, source_name, symbol, direction, setup, entry,
                 entry_zone_low, entry_zone_high, sl, tp1, tp2, tp3, timeframe, parse_status,
                 status, verdict, score, confidence, risk_level, expiry_minutes, raw_text,
                 reasons, rejection_reasons, payload, telegram_sent, send_reason, dedupe_key, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(signal_id) DO UPDATE SET
                status=excluded.status,
                verdict=excluded.verdict,
                score=excluded.score,
                confidence=excluded.confidence,
                risk_level=excluded.risk_level,
                reasons=excluded.reasons,
                rejection_reasons=excluded.rejection_reasons,
                payload=excluded.payload,
                telegram_sent=excluded.telegram_sent,
                send_reason=excluded.send_reason,
                updated_at=datetime('now')
            """,
            (
                signal.get("signal_id"),
                signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                signal.get("source_type") or "unknown",
                signal.get("source_name") or "unknown",
                signal.get("symbol"),
                signal.get("direction"),
                signal.get("setup"),
                signal.get("entry"),
                signal.get("entry_zone_low"),
                signal.get("entry_zone_high"),
                signal.get("sl"),
                signal.get("tp1"),
                signal.get("tp2"),
                signal.get("tp3"),
                signal.get("timeframe"),
                signal.get("parse_status") or "unparsed",
                signal.get("status") or "new",
                signal.get("verdict") or "WAIT_CONFIRMATION",
                float(signal.get("score") or 0),
                float(signal.get("confidence") or 0),
                signal.get("risk_level") or "HIGH",
                int(signal.get("expiry_minutes") or 20),
                signal.get("raw_text"),
                reasons,
                rejection_reasons,
                payload,
                1 if telegram_sent else 0,
                send_reason,
                signal.get("dedupe_key"),
            ),
        )
        if signal.get("dedupe_key"):
            conn.execute(
                "INSERT OR IGNORE INTO signal_dedupe (dedupe_key, signal_id) VALUES (?, ?)",
                (signal.get("dedupe_key"), signal.get("signal_id")),
            )
    refresh_source_reliability()
    return signal


def is_duplicate(dedupe_key: str | None) -> bool:
    if not dedupe_key:
        return False
    with db() as conn:
        row = conn.execute("SELECT dedupe_key FROM signal_dedupe WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    return bool(row)


def latest_signals(limit: int = 100, verdict: str | None = None, include_raw: bool = False) -> list[dict]:
    max_rows = max(1, min(int(limit or 100), 1000))
    where = ""
    params: tuple
    if verdict:
        where = "WHERE verdict = ?"
        params = (verdict, max_rows)
    else:
        params = (max_rows,)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM signal_events
            {where}
            ORDER BY timestamp DESC, created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [signal_row_to_payload(row, include_raw=include_raw) for row in rows]


def get_signal(signal_id: str, include_raw: bool = False) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM signal_events WHERE signal_id = ?", (signal_id,)).fetchone()
    return signal_row_to_payload(row, include_raw=include_raw) if row else None


def save_signal_evaluations(signal_id: str, evaluations: list[dict]) -> None:
    with db() as conn:
        for item in evaluations:
            payload = json.dumps(item, ensure_ascii=False, default=str)
            conn.execute(
                """
                INSERT INTO signal_evaluations
                    (signal_id, horizon, result, move_pct, r_multiple, evaluated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id, horizon) DO UPDATE SET
                    result=excluded.result,
                    move_pct=excluded.move_pct,
                    r_multiple=excluded.r_multiple,
                    evaluated_at=excluded.evaluated_at,
                    payload=excluded.payload
                """,
                (
                    signal_id,
                    item.get("horizon"),
                    item.get("result"),
                    item.get("move_pct"),
                    item.get("r_multiple"),
                    item.get("evaluated_at") or datetime.now(timezone.utc).isoformat(),
                    payload,
                ),
            )
    refresh_source_reliability()


def signal_evaluations(limit: int = 1000) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT e.*, s.symbol, s.direction, s.source_name, s.setup, s.score
            FROM signal_evaluations e
            LEFT JOIN signal_events s ON s.signal_id = e.signal_id
            ORDER BY e.evaluated_at DESC, e.created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 1000), 5000)),),
        ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        try:
            payload = json.loads(data.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload.update({key: data.get(key) for key in ("signal_id", "horizon", "result", "move_pct", "r_multiple", "evaluated_at", "symbol", "direction", "source_name", "setup", "score")})
        result.append(payload)
    return result


def source_reliability() -> list[dict]:
    stats = refresh_source_reliability()
    return sorted(stats, key=lambda item: (item.get("trust_score") is not None, item.get("trust_score") or -1, item.get("total_signals") or 0), reverse=True)


def refresh_source_reliability() -> list[dict]:
    signals = latest_signals(limit=5000, include_raw=False)
    evals = signal_evaluations(limit=5000)
    evals_by_signal = defaultdict(list)
    for item in evals:
        evals_by_signal[item.get("signal_id")].append(item)

    grouped: dict[str, dict] = {}
    for signal in signals:
        source = signal.get("source_name") or "unknown"
        bucket = grouped.setdefault(source, base_source_stats(source, signal.get("source_type")))
        bucket["total_signals"] += 1
        bucket["source_type"] = bucket.get("source_type") or signal.get("source_type")
        bucket["last_signal_at"] = max(str(bucket.get("last_signal_at") or ""), str(signal.get("timestamp") or ""))
        if signal.get("parse_status") == "parsed":
            bucket["parsed_count"] += 1
        if signal.get("verdict") == "VALID_SIGNAL":
            bucket["valid_count"] += 1
        elif signal.get("verdict") in {"WATCH_ONLY", "WAIT_CONFIRMATION"}:
            bucket["watch_count"] += 1
        elif signal.get("verdict") in {"REJECTED", "DUPLICATE"}:
            bucket["rejected_count"] += 1
        elif signal.get("verdict") == "EXPIRED":
            bucket["expired_count"] += 1
        symbol_results = defaultdict(lambda: {"correct": 0, "wrong": 0, "r": []})
        for evaluation in evals_by_signal.get(signal.get("signal_id"), []):
            if evaluation.get("result") in {"correct", "wrong", "neutral"}:
                bucket["evaluated_count"] += 1
            if evaluation.get("result") == "correct":
                bucket["correct_count"] += 1
                symbol_results[signal.get("symbol")]["correct"] += 1
            elif evaluation.get("result") == "wrong":
                bucket["wrong_count"] += 1
                symbol_results[signal.get("symbol")]["wrong"] += 1
            elif evaluation.get("result") == "neutral":
                bucket["neutral_count"] += 1
            if evaluation.get("r_multiple") is not None:
                bucket["_r_values"].append(float(evaluation.get("r_multiple") or 0))
        for symbol, values in symbol_results.items():
            if symbol:
                bucket["_symbols"][symbol]["correct"] += values["correct"]
                bucket["_symbols"][symbol]["wrong"] += values["wrong"]

    rows = []
    with db() as conn:
        for source, stats in grouped.items():
            finalize_source_stats(stats)
            rows.append(stats)
            conn.execute(
                """
                INSERT INTO signal_sources
                    (source_name, source_type, total_signals, parsed_count, valid_count, watch_count,
                     rejected_count, expired_count, triggered_count, evaluated_count, correct_count,
                     wrong_count, neutral_count, winrate, average_R, average_delay, best_symbols,
                     worst_symbols, trust_score, last_signal_at, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_name) DO UPDATE SET
                    source_type=excluded.source_type,
                    total_signals=excluded.total_signals,
                    parsed_count=excluded.parsed_count,
                    valid_count=excluded.valid_count,
                    watch_count=excluded.watch_count,
                    rejected_count=excluded.rejected_count,
                    expired_count=excluded.expired_count,
                    triggered_count=excluded.triggered_count,
                    evaluated_count=excluded.evaluated_count,
                    correct_count=excluded.correct_count,
                    wrong_count=excluded.wrong_count,
                    neutral_count=excluded.neutral_count,
                    winrate=excluded.winrate,
                    average_R=excluded.average_R,
                    average_delay=excluded.average_delay,
                    best_symbols=excluded.best_symbols,
                    worst_symbols=excluded.worst_symbols,
                    trust_score=excluded.trust_score,
                    last_signal_at=excluded.last_signal_at,
                    payload=excluded.payload,
                    updated_at=datetime('now')
                """,
                (
                    stats["source_name"],
                    stats.get("source_type"),
                    stats["total_signals"],
                    stats["parsed_count"],
                    stats["valid_count"],
                    stats["watch_count"],
                    stats["rejected_count"],
                    stats["expired_count"],
                    stats["triggered_count"],
                    stats["evaluated_count"],
                    stats["correct_count"],
                    stats["wrong_count"],
                    stats["neutral_count"],
                    stats["winrate"],
                    stats["average_R"],
                    stats["average_delay"],
                    json.dumps(stats["best_symbols"], ensure_ascii=False),
                    json.dumps(stats["worst_symbols"], ensure_ascii=False),
                    stats["trust_score"],
                    stats["last_signal_at"],
                    json.dumps(stats, ensure_ascii=False, default=str),
                ),
            )
    return rows


def base_source_stats(source_name: str, source_type: str | None) -> dict:
    return {
        "source_name": source_name,
        "source_type": source_type,
        "total_signals": 0,
        "parsed_count": 0,
        "valid_count": 0,
        "watch_count": 0,
        "rejected_count": 0,
        "expired_count": 0,
        "triggered_count": 0,
        "evaluated_count": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "neutral_count": 0,
        "winrate": None,
        "average_R": None,
        "average_delay": None,
        "best_symbols": [],
        "worst_symbols": [],
        "trust_score": None,
        "last_signal_at": None,
        "_r_values": [],
        "_symbols": defaultdict(lambda: {"correct": 0, "wrong": 0}),
    }


def finalize_source_stats(stats: dict) -> None:
    decisive = stats["correct_count"] + stats["wrong_count"]
    stats["winrate"] = round(stats["correct_count"] / decisive * 100, 1) if decisive else None
    stats["average_R"] = round(sum(stats["_r_values"]) / len(stats["_r_values"]), 2) if stats["_r_values"] else None
    if stats["evaluated_count"]:
        valid_ratio = stats["valid_count"] / max(1, stats["total_signals"])
        win_component = (stats["winrate"] or 0) * 0.7
        stats["trust_score"] = round(min(100, win_component + valid_ratio * 20 + min(10, stats["evaluated_count"])), 1)
    ranked = []
    for symbol, values in stats["_symbols"].items():
        total = values["correct"] + values["wrong"]
        if total:
            ranked.append({"symbol": symbol, "accuracy": round(values["correct"] / total * 100, 1), "samples": total})
    ranked.sort(key=lambda item: (item["accuracy"], item["samples"]), reverse=True)
    stats["best_symbols"] = ranked[:3]
    stats["worst_symbols"] = list(reversed(ranked[-3:])) if ranked else []
    stats.pop("_r_values", None)
    stats.pop("_symbols", None)


def signal_row_to_payload(row, include_raw: bool = False) -> dict:
    data = dict(row)
    try:
        payload = json.loads(data.get("payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    for key in PUBLIC_SIGNAL_FIELDS:
        if key in data:
            payload[key] = data[key]
    payload["telegram_sent"] = bool(data.get("telegram_sent"))
    payload["reasons"] = parse_json_list(data.get("reasons"))
    payload["rejection_reasons"] = parse_json_list(data.get("rejection_reasons"))
    if include_raw:
        payload["raw_text"] = data.get("raw_text")
    else:
        payload.pop("raw_text", None)
    return payload


def parse_json_list(value) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
