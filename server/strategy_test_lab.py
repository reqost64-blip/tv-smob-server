from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Optional
from zoneinfo import ZoneInfo

from . import bias_store
from .strategy_test_store import canonical_symbol, load_strategy_trades


BERLIN_TZ = ZoneInfo("Europe/Berlin")
LOW_SAMPLE_SIZE = 25


def build_strategy_lab_report(symbol: Optional[str] = None, bot_id: Optional[str] = None, include_bias_filter: bool = False) -> dict:
    raw_trades = load_strategy_trades(symbol=symbol, bot_id=bot_id)
    trades = [enrich_trade(row) for row in raw_trades]
    per_symbol = {}
    for sym in sorted({trade["symbol"] for trade in trades} | {canonical_symbol(symbol)} if symbol else {trade["symbol"] for trade in trades}):
        rows = [trade for trade in trades if trade["symbol"] == sym]
        if rows:
            per_symbol[sym] = symbol_report(sym, rows)
    total = risk_damage_analytics(trades)
    dangerous = [
        item for item in per_symbol.values()
        if item["risk_damage"]["risk_status"] in {"DANGEROUS", "CRITICAL", "BROKEN_RISK_MODEL"}
    ]
    critical = [
        item for item in per_symbol.values()
        if item["risk_damage"]["risk_status"] in {"CRITICAL", "BROKEN_RISK_MODEL"}
    ]
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"symbol": symbol, "bot_id": bot_id, "include_bias_filter": include_bias_filter},
        "data_sources": [
            "native_trade_journal",
            "native_mt5_closed_trades",
            "deal_reports",
            "history_deals",
        ],
        "trade_count": len(trades),
        "sample_warning": "LOW_SAMPLE_SIZE" if len(trades) < LOW_SAMPLE_SIZE else None,
        "risk_damage_analytics": total,
        "current_settings_diagnostics": current_settings_diagnostics(trades),
        "per_symbol": per_symbol,
        "dangerous_symbols": [item["symbol"] for item in dangerous],
        "critical_problems": critical_problems(total, per_symbol),
        "bias_context": latest_bias_context() if include_bias_filter else None,
    }


def enrich_trade(row: dict) -> dict:
    symbol = canonical_symbol(row.get("symbol"))
    side = str(row.get("side") or "").lower()
    entry = to_float(row.get("entry_price"))
    sl = to_float(row.get("stop_loss"))
    tp1 = to_float(row.get("tp1"))
    tp2 = to_float(row.get("tp2"))
    tp3 = to_float(row.get("tp3"))
    profit = to_float(row.get("result_money"))
    close_time = row.get("close_time")
    entry_time = row.get("entry_time")
    sl_distance = price_distance(entry, sl)
    tp1_distance = price_distance(entry, tp1)
    tp2_distance = price_distance(entry, tp2)
    tp3_distance = price_distance(entry, tp3)
    result_r = estimate_result_r(profit, row, sl_distance, tp1_distance)
    enriched = dict(row)
    enriched.update(
        {
            "symbol": symbol,
            "side": side,
            "entry_time": entry_time,
            "close_time": close_time,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit_levels": [value for value in (tp1, tp2, tp3) if value is not None],
            "profit": profit,
            "result_money": profit,
            "result_R": result_r,
            "sl_distance": sl_distance,
            "tp1_distance": tp1_distance,
            "tp2_distance": tp2_distance,
            "tp3_distance": tp3_distance,
            "risk_reward_ratio": safe_div(tp1_distance, sl_distance),
            "sl_tp1_ratio": safe_div(sl_distance, tp1_distance),
            "session": session_label(entry_time),
            "weekday": weekday_label(entry_time),
            "holding_time_minutes": holding_minutes(entry_time, close_time, row.get("duration_seconds")),
            "tp1_hit": bool(row.get("tp1_hit")),
            "tp2_hit": bool(row.get("tp2_hit")),
            "tp3_hit": bool(row.get("tp3_hit")),
            "be_activated": bool(row.get("be_activated")),
        }
    )
    return enriched


def symbol_report(symbol: str, trades: list[dict]) -> dict:
    return {
        "symbol": symbol,
        "sample_warning": "LOW_SAMPLE_SIZE" if len(trades) < LOW_SAMPLE_SIZE else None,
        "trade_count": len(trades),
        "risk_damage": risk_damage_analytics(trades),
        "tp_sl_diagnostics": tp_sl_diagnostics(trades),
        "recent_trades": trades[-20:],
    }


def risk_damage_analytics(trades: list[dict]) -> dict:
    profits = [to_float(t.get("result_money")) for t in trades if to_float(t.get("result_money")) is not None]
    r_values = [to_float(t.get("result_R")) for t in trades if to_float(t.get("result_R")) is not None]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    avg_daily_profit = average_daily_profit(trades)
    biggest_loss = min(losses) if losses else 0.0
    biggest_win = max(wins) if wins else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    loss_recovery_ratio = abs(avg_loss) / avg_daily_profit if avg_daily_profit > 0 and avg_loss < 0 else None
    single_loss_damage_days = abs(biggest_loss) / avg_daily_profit if avg_daily_profit > 0 and biggest_loss < 0 else None
    tp1_wins_needed = abs(biggest_loss) / avg_win if avg_win > 0 and biggest_loss < 0 else None
    equity = equity_curve(profits)
    max_dd = max_drawdown(equity)
    recovery = sum(profits) / max_dd if max_dd > 0 else None
    return {
        "trades": len(profits),
        "average_winning_trade": round(avg_win, 2) if wins else None,
        "average_losing_trade": round(avg_loss, 2) if losses else None,
        "biggest_loss": round(biggest_loss, 2) if losses else None,
        "biggest_win": round(biggest_win, 2) if wins else None,
        "winrate": round(len(wins) / len(profits) * 100, 1) if profits else 0.0,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else (999.0 if gross_profit > 0 else 0.0),
        "expectancy_money": round(sum(profits) / len(profits), 2) if profits else None,
        "expectancy_R": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "average_daily_profit": round(avg_daily_profit, 2) if avg_daily_profit else 0.0,
        "max_daily_loss": round(max_daily_loss(trades), 2),
        "max_drawdown": round(max_dd, 2),
        "max_losing_streak": max_losing_streak(profits),
        "recovery_factor": round(recovery, 2) if recovery is not None else None,
        "loss_recovery_ratio": round(loss_recovery_ratio, 2) if loss_recovery_ratio is not None else None,
        "single_loss_damage_days": round(single_loss_damage_days, 2) if single_loss_damage_days is not None else None,
        "tp1_wins_needed_to_recover_one_sl": round(tp1_wins_needed, 2) if tp1_wins_needed is not None else None,
        "risk_status": risk_status(single_loss_damage_days),
        "one_sl_erased_multiple_profitable_trades_or_days": bool(single_loss_damage_days and single_loss_damage_days > 1),
    }


def tp_sl_diagnostics(trades: list[dict]) -> dict:
    tp1 = [t["tp1_distance"] for t in trades if t.get("tp1_distance")]
    sl = [t["sl_distance"] for t in trades if t.get("sl_distance")]
    ratios = [t["sl_tp1_ratio"] for t in trades if t.get("sl_tp1_ratio") is not None]
    rr = [t["risk_reward_ratio"] for t in trades if t.get("risk_reward_ratio") is not None]
    total = len(ratios)
    return {
        "median_tp_distance": round(median(tp1), 5) if tp1 else None,
        "median_sl_distance": round(median(sl), 5) if sl else None,
        "sl_tp_ratio": round(median(ratios), 2) if ratios else None,
        "average_reward_risk": round(sum(rr) / len(rr), 3) if rr else None,
        "sl_more_than_3x_tp1_pct": pct(sum(1 for value in ratios if value > 3), total),
        "sl_more_than_5x_tp1_pct": pct(sum(1 for value in ratios if value > 5), total),
        "sl_more_than_10x_tp1_pct": pct(sum(1 for value in ratios if value > 10), total),
        "warning": "TP_TOO_SHORT_OR_SL_TOO_LARGE" if ratios and median(ratios) > 3 else None,
    }


def current_settings_diagnostics(trades: list[dict]) -> dict:
    by_symbol = {}
    for symbol in sorted({trade["symbol"] for trade in trades}):
        by_symbol[symbol] = tp_sl_diagnostics([trade for trade in trades if trade["symbol"] == symbol])
    return by_symbol


def critical_problems(total: dict, per_symbol: dict) -> list[dict]:
    problems = []
    if total.get("risk_status") in {"CRITICAL", "BROKEN_RISK_MODEL"}:
        problems.append({"scope": "TOTAL", "status": total.get("risk_status"), "single_loss_damage_days": total.get("single_loss_damage_days")})
    for symbol, report in per_symbol.items():
        risk = report["risk_damage"]
        diag = report["tp_sl_diagnostics"]
        if risk.get("risk_status") in {"CRITICAL", "BROKEN_RISK_MODEL"}:
            problems.append({"scope": symbol, "status": risk.get("risk_status"), "single_loss_damage_days": risk.get("single_loss_damage_days")})
        if diag.get("warning"):
            problems.append({"scope": symbol, "status": diag["warning"], "sl_tp_ratio": diag.get("sl_tp_ratio")})
    return problems


def monte_carlo_stress(trades: list[dict], iterations: int = 250) -> dict:
    profits = [to_float(t.get("sim_profit", t.get("result_money"))) for t in trades]
    profits = [p for p in profits if p is not None]
    if not profits:
        return {"iterations": 0}
    rng = random.Random(260515)
    drawdowns = []
    ruin = 0
    five_loss = 0
    ten_day_giveback = 0
    worst_month = []
    avg_day = max(average_daily_profit(trades), 0)
    for _ in range(iterations):
        sample = profits[:]
        rng.shuffle(sample)
        curve = equity_curve(sample)
        dd = max_drawdown(curve)
        drawdowns.append(dd)
        if curve and min(curve) < -max(abs(sum(profits)) * 0.5, avg_day * 10, 1):
            ruin += 1
        if has_losing_streak(sample, 5):
            five_loss += 1
        if avg_day > 0 and dd >= avg_day * 10:
            ten_day_giveback += 1
        chunks = [sum(sample[i:i + 20]) for i in range(0, len(sample), 20)]
        worst_month.append(min(chunks) if chunks else 0)
    return {
        "iterations": iterations,
        "worst_drawdown": round(max(drawdowns), 2),
        "probability_of_ruin": round(ruin / iterations * 100, 1),
        "probability_of_5_loss_streak": round(five_loss / iterations * 100, 1),
        "probability_of_giving_back_10_profitable_days": round(ten_day_giveback / iterations * 100, 1),
        "expected_worst_month": round(sum(worst_month) / len(worst_month), 2),
    }


def latest_bias_context() -> dict | None:
    report = bias_store.latest_bias_report()
    if not report:
        return None
    return {
        "macro_risk": report.get("macro_risk"),
        "symbols": [
            {
                "symbol": canonical_symbol(row.get("symbol")),
                "bias": row.get("bias"),
                "confidence": row.get("confidence"),
                "high_risk": row.get("high_risk"),
            }
            for row in report.get("symbols", [])
        ],
    }


def average_daily_profit(trades: list[dict]) -> float:
    daily = defaultdict(float)
    for trade in trades:
        profit = to_float(trade.get("result_money"))
        when = parse_dt(trade.get("close_time") or trade.get("entry_time"))
        if profit is None or not when:
            continue
        daily[when.astimezone(BERLIN_TZ).date().isoformat()] += profit
    positive_days = [value for value in daily.values() if value > 0]
    return sum(positive_days) / len(positive_days) if positive_days else 0.0


def max_daily_loss(trades: list[dict]) -> float:
    daily = defaultdict(float)
    for trade in trades:
        profit = to_float(trade.get("result_money"))
        when = parse_dt(trade.get("close_time") or trade.get("entry_time"))
        if profit is None or not when:
            continue
        daily[when.astimezone(BERLIN_TZ).date().isoformat()] += profit
    return min(daily.values()) if daily else 0.0


def estimate_result_r(profit: float | None, row: dict, sl_distance: float | None, tp1_distance: float | None) -> float | None:
    payload = row.get("payload") or {}
    for key in ("profit_r", "r_multiple"):
        value = to_float(payload.get(key) or row.get(key))
        if value is not None:
            return value
    if profit is None:
        return None
    if profit < 0:
        return -1.0
    if row.get("tp3_hit") and sl_distance and row.get("tp3"):
        return safe_div(price_distance(row.get("entry_price"), row.get("tp3")), sl_distance) or 1.0
    if row.get("tp2_hit") and sl_distance and row.get("tp2"):
        return safe_div(price_distance(row.get("entry_price"), row.get("tp2")), sl_distance) or 1.0
    if row.get("tp1_hit") and sl_distance and tp1_distance:
        return safe_div(tp1_distance, sl_distance) or 0.5
    return 0.25 if profit > 0 else 0.0


def risk_status(single_loss_damage_days: float | None) -> str:
    if single_loss_damage_days is None:
        return "UNKNOWN"
    if single_loss_damage_days > 10:
        return "BROKEN_RISK_MODEL"
    if single_loss_damage_days > 5:
        return "CRITICAL"
    if single_loss_damage_days > 3:
        return "DANGEROUS"
    return "OK"


def price_distance(a: object, b: object) -> float | None:
    first = to_float(a)
    second = to_float(b)
    if first is None or second is None:
        return None
    return abs(first - second)


def safe_div(a: object, b: object) -> float | None:
    first = to_float(a)
    second = to_float(b)
    if first is None or second in (None, 0):
        return None
    return first / second


def to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def session_label(value: object) -> str:
    parsed = parse_dt(value)
    if not parsed:
        return "UNKNOWN"
    hour = parsed.astimezone(BERLIN_TZ).hour
    if 8 <= hour < 14:
        return "EUROPE"
    if 14 <= hour < 22:
        return "US"
    return "ASIA_OVERNIGHT"


def weekday_label(value: object) -> str:
    parsed = parse_dt(value)
    return parsed.strftime("%A") if parsed else "UNKNOWN"


def holding_minutes(opened: object, closed: object, duration_seconds: object = None) -> float | None:
    seconds = to_float(duration_seconds)
    if seconds is not None:
        return round(seconds / 60, 1)
    start = parse_dt(opened)
    end = parse_dt(closed)
    if not start or not end:
        return None
    return round(max(0, (end - start).total_seconds()) / 60, 1)


def equity_curve(values: list[float]) -> list[float]:
    total = 0.0
    curve = []
    for value in values:
        total += value
        curve.append(total)
    return curve


def max_drawdown(curve: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def max_losing_streak(values: list[float]) -> int:
    best = 0
    current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        best = max(best, current)
    return best


def has_losing_streak(values: list[float], length: int) -> bool:
    return max_losing_streak(values) >= length


def pct(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0
