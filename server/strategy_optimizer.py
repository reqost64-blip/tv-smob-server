from __future__ import annotations

from itertools import product
from typing import Optional

from .strategy_test_lab import (
    LOW_SAMPLE_SIZE,
    build_strategy_lab_report,
    enrich_trade,
    max_drawdown,
    monte_carlo_stress,
    risk_damage_analytics,
    to_float,
)
from .strategy_test_store import load_strategy_trades, save_strategy_lab_run


PARTIAL_DISTS = {
    "100_TP1": (1.0, 0.0, 0.0),
    "75_25": (0.75, 0.25, 0.0),
    "70_20_10": (0.70, 0.20, 0.10),
    "60_30_10": (0.60, 0.30, 0.10),
    "50_30_20": (0.50, 0.30, 0.20),
}
BE_MODES = ("off", "entry", "entry_plus_0_1R", "entry_plus_spread")


def run_strategy_lab(
    symbol: Optional[str] = None,
    bot_id: Optional[str] = None,
    dry_run: bool = True,
    optimize: bool = True,
    include_bias_filter: bool = False,
) -> dict:
    report = build_strategy_lab_report(symbol=symbol, bot_id=bot_id, include_bias_filter=include_bias_filter)
    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "optimize": bool(optimize),
        "analysis": report,
        "recommendations": {},
        "rejected_settings": [],
        "requires_human_approval": True,
        "note": "Simulation only. No MT5 files, live trading flags, lot/risk/magic/bot presets are changed.",
    }
    if optimize:
        result.update(optimize_settings(symbol=symbol, bot_id=bot_id, include_bias_filter=include_bias_filter))
    run_id = save_strategy_lab_run(
        {
            "symbol": symbol,
            "bot_id": bot_id,
            "dry_run": dry_run,
            "optimize": optimize,
            "include_bias_filter": include_bias_filter,
        },
        result,
    )
    if run_id:
        result["run_id"] = run_id
    return result


def recommendations_payload(symbol: Optional[str] = None, bot_id: Optional[str] = None) -> dict:
    optimized = optimize_settings(symbol=symbol, bot_id=bot_id, include_bias_filter=True)
    return {
        "ok": True,
        "best_candidate_settings_per_symbol": optimized["recommendations"],
        "rejected_settings": optimized["rejected_settings"][:80],
        "risk_warnings": optimized["risk_warnings"],
        "sample_size_warning": optimized["sample_size_warning"],
        "requires_human_approval": True,
    }


def optimize_settings(symbol: Optional[str] = None, bot_id: Optional[str] = None, include_bias_filter: bool = False) -> dict:
    raw = [enrich_trade(row) for row in load_strategy_trades(symbol=symbol, bot_id=bot_id)]
    symbols = sorted({trade["symbol"] for trade in raw})
    recommendations = {}
    rejected = []
    warnings = []
    sample_warnings = {}
    for sym in symbols:
        trades = [trade for trade in raw if trade["symbol"] == sym]
        if len(trades) < LOW_SAMPLE_SIZE:
            sample_warnings[sym] = "LOW_SAMPLE_SIZE"
        candidates = candidate_grid(trades)
        scored = []
        for settings in candidates:
            sim_trades = simulate_settings(trades, settings, include_bias_filter=include_bias_filter)
            metrics = risk_damage_analytics(sim_trades)
            walk = walk_forward(sim_trades)
            score = score_candidate(metrics, sim_trades, walk)
            reasons = reject_reasons(metrics, sim_trades, walk)
            row = {
                "symbol": sym,
                "settings": settings,
                "score": round(score, 3),
                "metrics": metrics,
                "walk_forward": walk,
                "trade_count": len(sim_trades),
            }
            if reasons:
                row["reasons"] = reasons
                rejected.append(row)
            else:
                scored.append(row)
        scored.sort(key=lambda item: item["score"], reverse=True)
        if scored:
            best_sim = simulate_settings(trades, scored[0]["settings"], include_bias_filter=include_bias_filter)
            scored[0]["monte_carlo"] = monte_carlo_stress(best_sim, iterations=160)
            recommendations[sym] = scored[0]
        else:
            recommendations[sym] = {
                "symbol": sym,
                "settings": fallback_settings(trades),
                "score": 0,
                "metrics": risk_damage_analytics(trades),
                "reasons": ["NO_CANDIDATE_PASSED_MIN_FILTERS"],
            }
            warnings.append({"symbol": sym, "warning": "NO_CANDIDATE_PASSED_MIN_FILTERS"})
    return {
        "recommendations": recommendations,
        "rejected_settings": rejected,
        "risk_warnings": warnings,
        "sample_size_warning": sample_warnings or None,
    }


def candidate_grid(trades: list[dict]) -> list[dict]:
    sl_values = sorted({round(t["sl_distance"], 5) for t in trades if t.get("sl_distance")})
    if sl_values:
        sl_candidates = [percentile(sl_values, 0.5), percentile(sl_values, 0.7), percentile(sl_values, 0.85)]
    else:
        sl_candidates = [None]
    candidates = []
    for max_sl, min_tp1_r, tp1_r, tp2_r, tp3_r, partial, be in product(
        sl_candidates,
        (0.5, 0.8),
        (0.8, 1.0),
        (1.6, 2.0),
        (2.4,),
        ("75_25", "60_30_10", "50_30_20"),
        ("entry", "entry_plus_0_1R", "entry_plus_spread"),
    ):
        candidates.append(
            {
                "max_sl_points": round(max_sl, 5) if max_sl is not None else None,
                "max_sl_atr": None,
                "min_tp1_r": min_tp1_r,
                "tp1_R": tp1_r,
                "tp2_R": tp2_r,
                "tp3_R": tp3_r,
                "partial_close_distribution": partial,
                "be_after_tp1": be,
                "skip_bad_reward_risk": True,
                "skip_bias_conflict": False,
                "min_bias_confidence": None,
                "skip_macro_high_risk": False,
                "skip_orb_atr_too_large": False,
            }
        )
    return candidates


def simulate_settings(trades: list[dict], settings: dict, include_bias_filter: bool = False) -> list[dict]:
    output = []
    weights = PARTIAL_DISTS[settings["partial_close_distribution"]]
    for trade in trades:
        sl_distance = to_float(trade.get("sl_distance"))
        tp1_distance = to_float(trade.get("tp1_distance"))
        if settings.get("max_sl_points") and sl_distance and sl_distance > settings["max_sl_points"]:
            continue
        actual_tp1_r = trade.get("risk_reward_ratio")
        if settings.get("skip_bad_reward_risk") and actual_tp1_r is not None and actual_tp1_r < settings["min_tp1_r"]:
            continue
        simulated = dict(trade)
        hit1 = bool(trade.get("tp1_hit") or (trade.get("result_R") or 0) >= settings["tp1_R"])
        hit2 = bool(trade.get("tp2_hit") or (trade.get("result_R") or 0) >= settings["tp2_R"])
        hit3 = bool(trade.get("tp3_hit") or (trade.get("result_R") or 0) >= settings["tp3_R"])
        if to_float(trade.get("result_money")) is not None and to_float(trade.get("result_money")) < 0:
            r_value = 0.0 if hit1 and settings["be_after_tp1"] in {"entry", "entry_plus_spread"} else -1.0
            if hit1 and settings["be_after_tp1"] == "entry_plus_0_1R":
                r_value = 0.1
        else:
            r_value = 0.0
            if hit1:
                r_value += weights[0] * settings["tp1_R"]
            if hit2:
                r_value += weights[1] * settings["tp2_R"]
            if hit3:
                r_value += weights[2] * settings["tp3_R"]
            if not hit1 and trade.get("result_R") is not None:
                r_value = max(0.0, min(float(trade["result_R"]), settings["tp1_R"]))
        money_per_r = money_per_risk(trade)
        simulated["result_R"] = r_value
        simulated["sim_profit"] = round(r_value * money_per_r, 2)
        simulated["result_money"] = simulated["sim_profit"]
        simulated["tp1_hit"] = hit1
        simulated["tp2_hit"] = hit2
        simulated["tp3_hit"] = hit3
        output.append(simulated)
    return output


def walk_forward(trades: list[dict]) -> dict:
    split = max(1, int(len(trades) * 0.7))
    train = trades[:split]
    validation = trades[split:]
    train_metrics = risk_damage_analytics(train)
    validation_metrics = risk_damage_analytics(validation)
    valid = (
        (train_metrics.get("expectancy_R") or 0) > 0
        and (validation_metrics.get("expectancy_R") or 0) > 0
        and (validation_metrics.get("profit_factor") or 0) >= 1.0
    )
    return {
        "train_trades": len(train),
        "validation_trades": len(validation),
        "train": train_metrics,
        "validation": validation_metrics,
        "passed": bool(valid),
    }


def score_candidate(metrics: dict, trades: list[dict], walk: dict) -> float:
    expectancy = clamp((metrics.get("expectancy_R") or 0) / 1.0, -1, 2)
    profit_factor = clamp((metrics.get("profit_factor") or 0) / 2.5, 0, 2)
    dd = max_drawdown([sum([to_float(t.get("result_money")) or 0 for t in trades[:i + 1]]) for i in range(len(trades))])
    drawdown_score = 1 / (1 + dd / 1000)
    damage_days = metrics.get("single_loss_damage_days")
    damage_score = 1 if damage_days is None else max(0, 1 - damage_days / 3)
    trade_count_score = min(1, len(trades) / LOW_SAMPLE_SIZE)
    stability = 1 if walk.get("passed") else 0
    return (
        expectancy * 0.35
        + profit_factor * 0.20
        + drawdown_score * 0.15
        + damage_score * 0.15
        + trade_count_score * 0.10
        + stability * 0.05
    )


def reject_reasons(metrics: dict, trades: list[dict], walk: dict) -> list[str]:
    reasons = []
    if (metrics.get("profit_factor") or 0) <= 1.3:
        reasons.append("PROFIT_FACTOR_BELOW_1_3")
    if (metrics.get("expectancy_R") or 0) <= 0.10:
        reasons.append("EXPECTANCY_R_BELOW_0_10")
    if metrics.get("single_loss_damage_days") is not None and metrics["single_loss_damage_days"] >= 3:
        reasons.append("SINGLE_LOSS_DAMAGE_DAYS_TOO_HIGH")
    if len(trades) < LOW_SAMPLE_SIZE:
        reasons.append("LOW_SAMPLE_SIZE")
    if not walk.get("passed"):
        reasons.append("WALK_FORWARD_FAILED")
    return reasons


def fallback_settings(trades: list[dict]) -> dict:
    values = sorted(t["sl_distance"] for t in trades if t.get("sl_distance"))
    return {
        "max_sl_points": round(percentile(values, 0.5), 5) if values else None,
        "min_tp1_r": 0.8,
        "tp1_R": 0.8,
        "tp2_R": 1.6,
        "tp3_R": 2.4,
        "partial_close_distribution": "60_30_10",
        "be_after_tp1": "entry_plus_0_1R",
        "skip_bad_reward_risk": True,
    }


def money_per_risk(trade: dict) -> float:
    profit = abs(to_float(trade.get("result_money")) or 0)
    r_value = abs(to_float(trade.get("result_R")) or 0)
    if profit > 0 and r_value > 0:
        return max(profit / r_value, 1.0)
    losses = abs(profit)
    return max(losses, 1.0)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * p))))
    return values[index]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
