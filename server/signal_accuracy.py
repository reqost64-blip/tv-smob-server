from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import bias_store, signal_store


HORIZONS = {"15m": 15, "30m": 30, "60m": 60, "120m": 120}
NEUTRAL_MOVE_PCT = 0.03


def evaluate_signal_accuracy(limit: int = 1000) -> dict:
    signals = [
        signal for signal in signal_store.latest_signals(limit=limit, include_raw=False)
        if signal.get("verdict") in {"VALID_SIGNAL", "WATCH_ONLY", "WAIT_CONFIRMATION"}
    ]
    history_by_symbol = defaultdict(list)
    for row in bias_store.live_bias_history(limit=5000):
        symbol = str(row.get("symbol") or "").upper()
        ts = parse_time(row.get("timestamp"))
        price = to_float(row.get("current_price"))
        if symbol and ts and price is not None:
            history_by_symbol[symbol].append({"timestamp": ts, "price": price})
    for rows in history_by_symbol.values():
        rows.sort(key=lambda item: item["timestamp"])

    evaluations = []
    for signal in signals:
        symbol = str(signal.get("symbol") or "").upper()
        rows = history_by_symbol.get(symbol, [])
        signal_evals = [evaluate_one(signal, rows, horizon, minutes) for horizon, minutes in HORIZONS.items()]
        signal_store.save_signal_evaluations(signal.get("signal_id"), signal_evals)
        evaluations.extend(signal_evals)
    return accuracy_payload(evaluations, signals)


def accuracy_payload(evaluations: list[dict], signals: list[dict]) -> dict:
    return {
        "ok": True,
        "signal_count": len(signals),
        "evaluated_count": sum(1 for item in evaluations if item.get("result") in {"correct", "wrong", "neutral"}),
        "not_enough_data_count": sum(1 for item in evaluations if item.get("result") == "not_enough_data"),
        "overall": aggregate(evaluations),
        "by_symbol": aggregate_by(evaluations, "symbol"),
        "by_source": aggregate_by(evaluations, "source_name"),
        "by_setup": aggregate_by(evaluations, "setup"),
        "by_horizon": {horizon: aggregate([item for item in evaluations if item.get("horizon") == horizon])["all"] for horizon in HORIZONS},
        "samples": evaluations[:100],
    }


def evaluate_one(signal: dict, prices: list[dict], horizon: str, minutes: int) -> dict:
    start_time = parse_time(signal.get("timestamp"))
    direction = signal.get("direction")
    entry = to_float(signal.get("entry"))
    sl = to_float(signal.get("sl"))
    tp1 = to_float(signal.get("tp1"))
    base = {
        "signal_id": signal.get("signal_id"),
        "symbol": signal.get("symbol"),
        "direction": direction,
        "source_name": signal.get("source_name"),
        "setup": signal.get("setup"),
        "horizon": horizon,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "move_pct": None,
        "r_multiple": None,
    }
    if not start_time or entry is None or not prices:
        return {**base, "result": "not_enough_data"}
    end_time = start_time + timedelta(minutes=minutes)
    path = [row for row in prices if start_time < row["timestamp"] <= end_time]
    if not path:
        return {**base, "result": "not_enough_data"}
    if sl is not None and tp1 is not None:
        for row in path:
            price = row["price"]
            if direction == "LONG":
                if price <= sl:
                    return with_move(base, entry, price, "wrong", sl)
                if price >= tp1:
                    return with_move(base, entry, price, "correct", sl)
            else:
                if price >= sl:
                    return with_move(base, entry, price, "wrong", sl)
                if price <= tp1:
                    return with_move(base, entry, price, "correct", sl)
    final_price = path[-1]["price"]
    move_pct = (final_price - entry) / entry * 100 if entry else 0
    if abs(move_pct) < NEUTRAL_MOVE_PCT:
        result = "neutral"
    elif direction == "LONG":
        result = "correct" if move_pct > 0 else "wrong"
    else:
        result = "correct" if move_pct < 0 else "wrong"
    return with_move(base, entry, final_price, result, sl)


def with_move(base: dict, entry: float, price: float, result: str, sl: Optional[float]) -> dict:
    move_pct = (price - entry) / entry * 100 if entry else None
    risk = abs(entry - sl) if sl is not None else None
    r_multiple = None
    if risk:
        signed_move = price - entry if base.get("direction") == "LONG" else entry - price
        r_multiple = signed_move / risk
    return {**base, "result": result, "move_pct": round(move_pct, 4) if move_pct is not None else None, "r_multiple": round(r_multiple, 3) if r_multiple is not None else None}


def aggregate_by(evaluations: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for item in evaluations:
        groups[str(item.get(key) or "UNKNOWN")].append(item)
    return {name: aggregate(items) for name, items in sorted(groups.items())}


def aggregate(evaluations: list[dict]) -> dict:
    stats = {"all": {"correct": 0, "wrong": 0, "neutral": 0, "not_enough_data": 0, "samples": 0, "accuracy": None}}
    for item in evaluations:
        result = item.get("result") or "not_enough_data"
        stats["all"][result] = stats["all"].get(result, 0) + 1
    decisive = stats["all"]["correct"] + stats["all"]["wrong"]
    stats["all"]["samples"] = decisive + stats["all"]["neutral"]
    stats["all"]["accuracy"] = round(stats["all"]["correct"] / decisive * 100, 1) if decisive else None
    return stats


def parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
