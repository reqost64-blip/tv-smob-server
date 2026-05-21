from __future__ import annotations

from .live_bias_engine import calculate_live_bias_report


SCANNER_SETUPS = [
    "VWAP_RECLAIM",
    "VWAP_REJECTION",
    "LIQUIDITY_SWEEP_REVERSAL",
    "MOMENTUM_SCALP",
    "EMA_TREND_CONTINUATION",
    "COMPRESSION_BREAKOUT",
    "BREAKOUT_RETEST",
]


def scan_market(allow_network: bool = False, symbol: str | None = None) -> dict:
    if not allow_network:
        return {
            "ok": True,
            "scanner_result": "unavailable",
            "source_availability": {"market_data": False, "reason": "network disabled"},
            "signals": [],
        }
    report = calculate_live_bias_report(allow_network=True, symbol=symbol)
    signals = []
    for row in report.get("symbols") or []:
        price = row.get("current_price")
        if not price:
            continue
        direction = row.get("direction")
        factor_scores = row.get("factor_scores") or {}
        setup = setup_from_factors(factor_scores, direction)
        signals.append(scanner_signal_from_bias(row, setup))
    return {
        "ok": True,
        "scanner_result": "ok" if signals else "unavailable",
        "source_availability": report.get("source_availability") or {},
        "signals": signals,
    }


def scanner_signal_from_bias(row: dict, setup: str) -> dict:
    price = float(row.get("current_price"))
    direction = row.get("direction")
    is_long = direction == "LONG"
    risk_pct = 0.0018 if row.get("risk") == "LOW" else 0.0025
    reward_pct = risk_pct * 1.35
    sl = price * (1 - risk_pct if is_long else 1 + risk_pct)
    tp1 = price * (1 + reward_pct if is_long else 1 - reward_pct)
    tp2 = price * (1 + reward_pct * 1.8 if is_long else 1 - reward_pct * 1.8)
    return {
        "source": "Market Scanner" if row.get("symbol") != "BTCUSD" else "Binance Scanner",
        "symbol": row.get("symbol"),
        "direction": direction,
        "setup": setup,
        "entry": round(price, 5),
        "entry_zone_low": round(price * 0.9998, 5),
        "entry_zone_high": round(price * 1.0002, 5),
        "sl": round(sl, 5),
        "tp1": round(tp1, 5),
        "tp2": round(tp2, 5),
        "tp3": None,
        "timeframe": "5m",
        "confidence": row.get("confidence"),
        "message": "scanner candidate from live bias and market confirmation",
        "confirmations": confirmations_from_bias(row),
        "current_price": round(price, 5),
    }


def setup_from_factors(factors: dict, direction: str | None) -> str:
    ordered = sorted(((key, abs(float(value or 0))) for key, value in factors.items()), key=lambda item: item[1], reverse=True)
    top = ordered[0][0] if ordered else ""
    if top == "vwap":
        return "VWAP_RECLAIM" if direction == "LONG" else "VWAP_REJECTION"
    if top == "momentum":
        return "MOMENTUM_SCALP"
    if top == "trend":
        return "EMA_TREND_CONTINUATION"
    if top == "volatility":
        return "COMPRESSION_BREAKOUT"
    if top == "market_structure":
        return "BREAKOUT_RETEST"
    return "MOMENTUM_SCALP"


def confirmations_from_bias(row: dict) -> list[str]:
    confirmations = [f"Live Bias {row.get('direction')} {row.get('confidence')}%"]
    factors = row.get("factor_scores") or {}
    if abs(float(factors.get("vwap") or 0)) >= 10:
        confirmations.append("VWAP confirmation")
    if abs(float(factors.get("momentum") or 0)) >= 10:
        confirmations.append("M5 momentum confirmation")
    if abs(float(factors.get("trend") or 0)) >= 10:
        confirmations.append("EMA trend confirmation")
    return confirmations[:4]
