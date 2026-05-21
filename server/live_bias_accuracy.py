from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import bias_store


HORIZONS = {
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
}
CONFIDENCE_BUCKETS = [
    ("51-55", 51, 55),
    ("56-60", 56, 60),
    ("61-65", 61, 65),
    ("66-70", 66, 70),
    ("70+", 71, 100),
]
DATA_QUALITY_BUCKETS = [
    ("<50", 0, 49.999),
    ("50-69", 50, 69.999),
    ("70+", 70, 100),
]
NEUTRAL_MOVE_PCT = 0.03


def live_bias_accuracy(symbol: str | None = None, limit: int = 5000) -> dict:
    snapshots = bias_store.live_bias_history(symbol=symbol, limit=limit)
    evaluations = evaluate_snapshots(snapshots)
    return {
        "ok": True,
        "horizons": list(HORIZONS.keys()),
        "neutral_move_pct": NEUTRAL_MOVE_PCT,
        "snapshot_count": len(snapshots),
        "evaluated_count": sum(1 for item in evaluations if item["result"] in {"correct", "wrong", "neutral"}),
        "not_enough_data_count": sum(1 for item in evaluations if item["result"] == "not_enough_data"),
        "overall": aggregate(evaluations),
        "by_symbol": aggregate_by(evaluations, "symbol"),
        "by_direction": aggregate_by(evaluations, "direction"),
        "by_confidence_bucket": aggregate_by(evaluations, "confidence_bucket"),
        "by_risk": aggregate_by(evaluations, "risk"),
        "by_data_quality": aggregate_by(evaluations, "data_quality_bucket"),
        "factor_stats": factor_stats(evaluations),
        "samples": evaluations[:80],
    }


def live_bias_calibration(symbol: str | None = None, limit: int = 5000) -> dict:
    accuracy = live_bias_accuracy(symbol=symbol, limit=limit)
    by_symbol = accuracy["by_symbol"]
    by_factor = accuracy["factor_stats"]
    best_symbol = best_group(by_symbol)
    worst_symbol = worst_group(by_symbol)
    strong_factors = [
        {"factor": key, **value}
        for key, value in by_factor.items()
        if value.get("aligned_samples", 0) >= 5 and value.get("aligned_accuracy") is not None and value["aligned_accuracy"] >= 55
    ]
    weak_factors = [
        {"factor": key, **value}
        for key, value in by_factor.items()
        if value.get("aligned_samples", 0) >= 5 and value.get("aligned_accuracy") is not None and value["aligned_accuracy"] < 45
    ]
    suggestions = []
    for item in sorted(strong_factors, key=lambda row: row["aligned_accuracy"], reverse=True)[:4]:
        suggestions.append(
            {
                "type": "weight_increase_candidate",
                "factor": item["factor"],
                "suggested_change": "+0.02",
                "reason": f"Aligned {item['factor']} signals show {item['aligned_accuracy']}% accuracy",
            }
        )
    for item in sorted(weak_factors, key=lambda row: row["aligned_accuracy"])[:4]:
        suggestions.append(
            {
                "type": "weight_decrease_candidate",
                "factor": item["factor"],
                "suggested_change": "-0.02",
                "reason": f"Aligned {item['factor']} signals show only {item['aligned_accuracy']}% accuracy",
            }
        )
    low_confidence = accuracy["by_confidence_bucket"].get("51-55", {})
    high_confidence = accuracy["by_confidence_bucket"].get("70+", {})
    if low_confidence.get("samples", 0) >= 5 and low_confidence.get("accuracy") is not None and low_confidence["accuracy"] < 45:
        suggestions.append(
            {
                "type": "confidence_filter_candidate",
                "factor": "confidence",
                "suggested_change": "treat 51-55 as weak/no-trade context",
                "reason": "Low confidence bucket underperforms",
            }
        )
    if high_confidence.get("samples", 0) >= 5 and high_confidence.get("accuracy") is not None and high_confidence["accuracy"] < 55:
        suggestions.append(
            {
                "type": "confidence_cap_review",
                "factor": "confidence",
                "suggested_change": "review confidence calibration above 70",
                "reason": "High confidence bucket is not clearly outperforming",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "type": "collect_more_data",
                "factor": "sample_size",
                "suggested_change": "no weight change yet",
                "reason": "Not enough evaluated snapshots for reliable calibration",
            }
        )
    return {
        "ok": True,
        "requires_human_approval": True,
        "snapshot_count": accuracy["snapshot_count"],
        "evaluated_count": accuracy["evaluated_count"],
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "factors_often_correct": sorted(strong_factors, key=lambda row: row["aligned_accuracy"], reverse=True),
        "factors_often_wrong": sorted(weak_factors, key=lambda row: row["aligned_accuracy"]),
        "symbol_rankings": rank_groups(by_symbol),
        "risk_level_stats": accuracy["by_risk"],
        "confidence_bucket_stats": accuracy["by_confidence_bucket"],
        "recommended_weight_adjustments": suggestions,
        "note": "Suggestions are read-only. No weights are changed automatically.",
    }


def evaluate_snapshots(snapshots: list[dict]) -> list[dict]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in snapshots:
        symbol = str(row.get("symbol") or "").upper()
        ts = parse_time(row.get("timestamp") or row.get("created_at"))
        price = to_float(row.get("current_price"))
        if symbol and ts:
            item = dict(row)
            item["_parsed_time"] = ts
            item["_price"] = price
            by_symbol[symbol].append(item)
    for rows in by_symbol.values():
        rows.sort(key=lambda item: item["_parsed_time"])

    evaluations: list[dict] = []
    for symbol, rows in by_symbol.items():
        for row in rows:
            for horizon, minutes in HORIZONS.items():
                evaluations.append(evaluate_one(symbol, row, rows, horizon, minutes))
    evaluations.sort(key=lambda item: (item.get("timestamp") or "", item.get("symbol") or "", item.get("horizon") or ""))
    return evaluations


def evaluate_one(symbol: str, row: dict, rows: list[dict], horizon: str, minutes: int) -> dict:
    start_time = row["_parsed_time"]
    start_price = row.get("_price")
    future = future_snapshot(rows, start_time + timedelta(minutes=minutes), tolerance_minutes=max(20, minutes // 2))
    direction = "LONG" if row.get("direction") == "LONG" else "SHORT"
    base = {
        "symbol": symbol,
        "timestamp": start_time.isoformat(),
        "horizon": horizon,
        "direction": direction,
        "confidence": int(row.get("confidence") or 0),
        "confidence_bucket": confidence_bucket(row.get("confidence")),
        "risk": row.get("risk") or "UNKNOWN",
        "data_quality_score": to_float(row.get("data_quality_score")) or 0.0,
        "data_quality_bucket": data_quality_bucket(row.get("data_quality_score")),
        "factor_scores": row.get("factor_scores") or {},
        "start_price": start_price,
        "future_price": future.get("_price") if future else None,
    }
    if start_price is None or start_price <= 0 or not future or future.get("_price") is None:
        return {**base, "result": "not_enough_data", "move_pct": None}
    future_price = future["_price"]
    move_pct = (future_price - start_price) / start_price * 100
    if abs(move_pct) < NEUTRAL_MOVE_PCT:
        result = "neutral"
    elif direction == "LONG":
        result = "correct" if move_pct > 0 else "wrong"
    else:
        result = "correct" if move_pct < 0 else "wrong"
    return {**base, "result": result, "move_pct": round(move_pct, 4)}


def future_snapshot(rows: list[dict], target: datetime, tolerance_minutes: int) -> Optional[dict]:
    max_time = target + timedelta(minutes=tolerance_minutes)
    for row in rows:
        row_time = row["_parsed_time"]
        if target <= row_time <= max_time and row.get("_price") is not None:
            return row
    return None


def aggregate(evaluations: list[dict]) -> dict:
    result = {horizon: empty_stats() for horizon in HORIZONS}
    result["all"] = empty_stats()
    for item in evaluations:
        add_result(result[item["horizon"]], item["result"])
        add_result(result["all"], item["result"])
    for stats in result.values():
        finalize_stats(stats)
    return result


def aggregate_by(evaluations: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in evaluations:
        groups[str(item.get(key) or "UNKNOWN")].append(item)
    return {group: aggregate(items) for group, items in sorted(groups.items())}


def factor_stats(evaluations: list[dict]) -> dict:
    stats: dict[str, dict] = defaultdict(lambda: {"samples": 0, "aligned_samples": 0, "aligned_correct": 0, "correct_scores": [], "wrong_scores": []})
    for item in evaluations:
        if item.get("result") not in {"correct", "wrong", "neutral"}:
            continue
        direction_sign = 1 if item.get("direction") == "LONG" else -1
        for factor, raw_score in (item.get("factor_scores") or {}).items():
            score = to_float(raw_score)
            if score is None:
                continue
            bucket = stats[factor]
            bucket["samples"] += 1
            if score * direction_sign > 0 and abs(score) >= 10:
                bucket["aligned_samples"] += 1
                if item["result"] == "correct":
                    bucket["aligned_correct"] += 1
            if item["result"] == "correct":
                bucket["correct_scores"].append(score)
            elif item["result"] == "wrong":
                bucket["wrong_scores"].append(score)
    result = {}
    for factor, values in stats.items():
        aligned_samples = values["aligned_samples"]
        result[factor] = {
            "samples": values["samples"],
            "aligned_samples": aligned_samples,
            "aligned_accuracy": round(values["aligned_correct"] / aligned_samples * 100, 1) if aligned_samples else None,
            "avg_correct_score": average(values["correct_scores"]),
            "avg_wrong_score": average(values["wrong_scores"]),
        }
    return dict(sorted(result.items()))


def empty_stats() -> dict:
    return {"correct": 0, "wrong": 0, "neutral": 0, "not_enough_data": 0, "samples": 0, "accuracy": None}


def add_result(stats: dict, result: str) -> None:
    stats[result] = int(stats.get(result, 0)) + 1


def finalize_stats(stats: dict) -> None:
    evaluated = stats["correct"] + stats["wrong"]
    stats["samples"] = evaluated + stats["neutral"]
    stats["accuracy"] = round(stats["correct"] / evaluated * 100, 1) if evaluated else None


def rank_groups(groups: dict) -> list[dict]:
    rows = []
    for name, payload in groups.items():
        stats = payload.get("all", {})
        if stats.get("accuracy") is not None:
            rows.append({"name": name, "accuracy": stats["accuracy"], "samples": stats.get("samples", 0)})
    return sorted(rows, key=lambda row: (row["accuracy"], row["samples"]), reverse=True)


def best_group(groups: dict) -> Optional[dict]:
    ranked = rank_groups(groups)
    return ranked[0] if ranked else None


def worst_group(groups: dict) -> Optional[dict]:
    ranked = rank_groups(groups)
    return ranked[-1] if ranked else None


def confidence_bucket(value) -> str:
    number = int(to_float(value) or 0)
    for label, low, high in CONFIDENCE_BUCKETS:
        if low <= number <= high:
            return label
    return "UNKNOWN"


def data_quality_bucket(value) -> str:
    number = float(to_float(value) or 0)
    for label, low, high in DATA_QUALITY_BUCKETS:
        if low <= number <= high:
            return label
    return "UNKNOWN"


def parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: list[float]) -> Optional[float]:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 2) if clean else None
