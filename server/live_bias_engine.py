from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .bias_engine import BIAS_SYMBOLS, BERLIN_TZ, INTERMARKET_TICKERS, NY_TZ
from .bias_sources import SeriesBundle, yahoo_chart_series, load_bias_source_context


LIVE_BIAS_VERSION = "live-bias-v2"
LIVE_TIMEFRAMES = {
    "D1": {"interval": "1d", "range": "1y", "weight": 0.18},
    "H4": {"interval": "1h", "range": "90d", "weight": 0.24},
    "H1": {"interval": "1h", "range": "60d", "weight": 0.30},
    "M15": {"interval": "15m", "range": "10d", "weight": 0.18},
    "M5": {"interval": "5m", "range": "5d", "weight": 0.10},
}
LIVE_FACTOR_WEIGHTS = {
    "market_structure": 0.25,
    "trend": 0.15,
    "vwap": 0.15,
    "momentum": 0.15,
    "volatility": 0.10,
    "intermarket": 0.15,
    "macro_news": 0.05,
}

_SOURCE_CACHE: dict[str, tuple[float, dict]] = {}
_SOURCE_CACHE_TTL_SECONDS = 240


def calculate_live_bias_report(
    allow_network: bool = True,
    now: Optional[datetime] = None,
    symbol: str | None = None,
    source_context: dict | None = None,
) -> dict:
    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    ny_now = now_utc.astimezone(NY_TZ)
    berlin_now = now_utc.astimezone(BERLIN_TZ)
    symbols = _filtered_symbols(symbol)
    context = source_context or load_live_bias_source_context(symbols, allow_network=allow_network)
    rows = [calculate_live_symbol_bias(item, context) for item in symbols]
    quality_values = [float(row.get("data_quality_score") or 0) for row in rows]
    risk = aggregate_risk(rows)
    report = {
        "ok": True,
        "version": LIVE_BIAS_VERSION,
        "timestamp": now_utc.isoformat(),
        "ny_time": ny_now.strftime("%H:%M %Z"),
        "berlin_time": berlin_now.strftime("%H:%M %Z"),
        "update_frequency": "5m active market / 15m outside active market",
        "macro_risk": risk,
        "risk": risk,
        "data_quality_score": round(sum(quality_values) / len(quality_values), 1) if quality_values else 0.0,
        "source_availability": context.get("source_availability") or {},
        "source_details": context.get("source_details") or {},
        "symbols": rows,
    }
    report["telegram_text"] = format_live_bias_telegram_message(report)
    return report


def load_live_bias_source_context(symbol_items: list[dict], allow_network: bool = True) -> dict:
    if not allow_network:
        base = load_bias_source_context(symbol_items, INTERMARKET_TICKERS, allow_network=False)
        base["timeframes"] = {}
        base["timeframe_availability"] = {}
        return base

    cache_key = "live:" + ",".join(sorted(item["symbol"] for item in symbol_items))
    cached = _SOURCE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _SOURCE_CACHE_TTL_SECONDS:
        return cached[1]

    base = load_bias_source_context(symbol_items, INTERMARKET_TICKERS, allow_network=True)
    timeframes: dict[str, dict[str, SeriesBundle]] = {}
    timeframe_availability: dict[str, dict[str, str]] = {}
    for item in symbol_items:
        symbol = item["symbol"]
        ticker = item["ticker"]
        timeframes[symbol] = {}
        timeframe_availability[symbol] = {}
        for tf, cfg in LIVE_TIMEFRAMES.items():
            try:
                bundle = yahoo_chart_series(ticker, interval=cfg["interval"], range_value=cfg["range"])
            except Exception:
                bundle = None
            if tf == "H4" and bundle:
                bundle = resample_bundle(bundle, 4, "yahoo_chart_h4")
            if bundle and len(bundle.closes) >= 20:
                timeframes[symbol][tf] = bundle
                timeframe_availability[symbol][tf] = "available"
            else:
                timeframe_availability[symbol][tf] = "unavailable"
    base["timeframes"] = timeframes
    base["timeframe_availability"] = timeframe_availability
    _SOURCE_CACHE[cache_key] = (time.time(), base)
    return base


def calculate_live_symbol_bias(item: dict, context: dict) -> dict:
    symbol = item["symbol"]
    tf_bundles = dict((context.get("timeframes") or {}).get(symbol) or {})
    if not tf_bundles:
        fallback = (context.get("market_data") or {}).get(item.get("ticker"))
        if fallback:
            tf_bundles["H1"] = fallback

    macro = context.get("macro") or {}
    news = context.get("news") or {}
    binance = context.get("binance") or {}
    factors = {
        "market_structure": market_structure_score(tf_bundles),
        "trend": trend_score(tf_bundles),
        "vwap": vwap_score(tf_bundles),
        "momentum": momentum_score(tf_bundles),
        "volatility": volatility_score(tf_bundles),
        "intermarket": intermarket_score(symbol, context.get("market_data") or {}, binance),
        "macro_news": macro_news_score(macro, news),
    }
    available = {key: value for key, value in factors.items() if value is not None and math.isfinite(value)}
    if available:
        total_weight = sum(LIVE_FACTOR_WEIGHTS[key] for key in available)
        final_score = sum(score * LIVE_FACTOR_WEIGHTS[key] for key, score in available.items()) / (total_weight or 1)
    else:
        final_score = 0.0

    source_availability = symbol_source_availability(symbol, tf_bundles, context, factors)
    data_quality = live_data_quality_score(source_availability, available, tf_bundles, macro, news, binance)
    agreement = factor_agreement(list(available.values()), final_score)
    timeframe_alignment = timeframe_trend_alignment(tf_bundles)
    volatility_state = volatility_regime(tf_bundles)
    macro_risk = str(macro.get("risk") or "UNKNOWN").upper()
    direction = "LONG" if final_score >= 0 else "SHORT"
    confidence, long_probability, short_probability = live_probabilities(
        final_score=final_score,
        direction=direction,
        data_quality=data_quality,
        agreement=agreement,
        timeframe_alignment=timeframe_alignment,
        volatility_state=volatility_state,
        macro_risk=macro_risk,
        source_availability=source_availability,
    )
    strength = confidence_strength(confidence, data_quality, agreement)
    risk, flags = live_risk_flags(data_quality, macro_risk, volatility_state, agreement, source_availability)
    row = {
        "symbol": symbol,
        "bot_id": item.get("bot_id"),
        "direction": direction,
        "bias": direction,
        "confidence": confidence,
        "long_probability": long_probability,
        "short_probability": short_probability,
        "long_percent": long_probability,
        "short_percent": short_probability,
        "final_score": round(final_score, 1),
        "score": round(final_score, 1),
        "strength": strength,
        "risk": risk,
        "risk_flags": flags,
        "flags": flags,
        "data_quality_score": data_quality,
        "factor_scores": {key: round(value, 1) if value is not None else None for key, value in factors.items()},
        "source_availability": source_availability,
        "timeframe_alignment": round(timeframe_alignment, 2),
        "factor_agreement": round(agreement, 2),
        "volatility_state": volatility_state,
        "invalidation": invalidation_info(direction, tf_bundles),
        "reasons": live_reasons(symbol, direction, tf_bundles, factors, macro, news, binance, volatility_state, flags),
    }
    return row


def format_live_bias_telegram_message(report: dict) -> str:
    lines = ["📈 LIVE MARKET BIAS", ""]
    for row in report.get("symbols") or []:
        symbol = str(row.get("symbol") or "—")
        direction = "LONG" if row.get("direction") == "LONG" else "SHORT"
        confidence = int(row.get("confidence") or 0)
        suffix = ""
        if row.get("strength") == "WEAK":
            suffix += " ⚠️ weak"
        if row.get("risk") == "HIGH":
            suffix += " ⚠️ high risk"
        lines.append(f"{symbol:<7} {direction:<5} {confidence}%{suffix}")
    lines.extend(
        [
            "",
            f"Risk: {report.get('risk') or report.get('macro_risk') or 'UNKNOWN'}",
            f"Data Quality: {int(round(float(report.get('data_quality_score') or 0)))}%",
            f"Updated: {_short_time(report.get('timestamp'))}",
        ]
    )
    high_risk = [row for row in report.get("symbols") or [] if row.get("risk") == "HIGH"]
    if high_risk:
        flags = sorted({flag for row in high_risk for flag in (row.get("risk_flags") or [])})
        lines.extend(["", "⚠️ HIGH RISK: " + ("/".join(flags[:4]) if flags else "macro/news/volatility")])
    unavailable = [key for key, value in (report.get("source_availability") or {}).items() if value == "unavailable"]
    if unavailable:
        lines.append("Unavailable: " + ", ".join(unavailable[:5]))
    return "\n".join(lines)


def live_bias_send_decision(report: dict, previous_rows: list[dict], force_send: bool = False) -> tuple[bool, str]:
    if force_send:
        return True, "force_send"
    previous = {str(row.get("symbol") or "").upper(): row for row in previous_rows or []}
    reasons: list[str] = []
    for row in report.get("symbols") or []:
        symbol = str(row.get("symbol") or "").upper()
        old = previous.get(symbol)
        if not old:
            continue
        old_direction = old.get("direction") or old.get("bias")
        new_direction = row.get("direction")
        old_conf = int(old.get("confidence") or 0)
        new_conf = int(row.get("confidence") or 0)
        old_risk = old.get("risk")
        new_risk = row.get("risk")
        if old_direction in {"LONG", "SHORT"} and new_direction in {"LONG", "SHORT"} and old_direction != new_direction:
            reasons.append(f"{symbol}_direction_flip")
        if abs(new_conf - old_conf) >= 8:
            reasons.append(f"{symbol}_confidence_change")
        if old_conf < 60 <= new_conf:
            reasons.append(f"{symbol}_confidence_above_60")
        if old_risk != "HIGH" and new_risk == "HIGH":
            reasons.append(f"{symbol}_risk_high")
    if reasons:
        return True, ",".join(reasons[:8])
    return False, "no_material_change"


def active_market_update_interval_seconds(now: Optional[datetime] = None) -> int:
    current = (now or datetime.now(timezone.utc)).astimezone(NY_TZ)
    active = current.weekday() < 5 and 4 <= current.hour < 17
    return 300 if active else 900


def resample_bundle(bundle: SeriesBundle, group_size: int, source: str) -> Optional[SeriesBundle]:
    if group_size <= 1:
        return bundle
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    for start in range(0, len(bundle.closes), group_size):
        end = start + group_size
        if end > len(bundle.closes):
            continue
        closes.append(bundle.closes[end - 1])
        highs.append(max(bundle.highs[start:end]))
        lows.append(min(bundle.lows[start:end]))
        volumes.append(sum(bundle.volumes[start:end]))
    if not closes:
        return None
    return SeriesBundle(closes=closes, highs=highs, lows=lows, volumes=volumes, source=source)


def market_structure_score(tf_bundles: dict[str, SeriesBundle]) -> Optional[float]:
    scores = []
    for tf, bundle in tf_bundles.items():
        if len(bundle.closes) < 20:
            continue
        highs = bundle.highs
        lows = bundle.lows
        closes = bundle.closes
        swing_high = max(highs[-20:-1])
        swing_low = min(lows[-20:-1])
        mid = (swing_high + swing_low) / 2
        score = 0.0
        score += 22 if closes[-1] > mid else -22
        score += 18 if highs[-1] >= swing_high else -18 if lows[-1] <= swing_low else 0
        score += 16 if higher_highs_lows(highs, lows) > 0 else -16 if higher_highs_lows(highs, lows) < 0 else 0
        score += 10 if closes[-1] > ema(closes, min(50, len(closes))) else -10
        scores.append(weighted_tf_score(tf, score))
    return weighted_average(scores)


def trend_score(tf_bundles: dict[str, SeriesBundle]) -> Optional[float]:
    scores = []
    for tf, bundle in tf_bundles.items():
        closes = bundle.closes
        if len(closes) < 30:
            continue
        ema20 = ema(closes, 20)
        ema50 = ema(closes, min(50, len(closes)))
        ema200 = ema(closes, min(200, len(closes)))
        slope = ema_slope(closes, 20)
        score = 0.0
        score += 28 if closes[-1] > ema20 else -28
        score += 24 if ema20 > ema50 else -24
        score += 18 if ema50 > ema200 else -18
        score += clamp(slope * 180, -22, 22)
        distance = (closes[-1] - ema20) / (ema20 or closes[-1]) * 100
        score += clamp(distance * 8, -8, 8)
        scores.append(weighted_tf_score(tf, score))
    return weighted_average(scores)


def vwap_score(tf_bundles: dict[str, SeriesBundle]) -> Optional[float]:
    scores = []
    for tf in ("H1", "M15", "M5"):
        bundle = tf_bundles.get(tf)
        if not bundle or len(bundle.closes) < 12:
            continue
        vwap = approximate_vwap(bundle, min(len(bundle.closes), 48 if tf == "H1" else 96))
        if not vwap:
            continue
        distance = (bundle.closes[-1] - vwap) / vwap * 100
        slope = vwap_slope(bundle)
        score = clamp(distance * 18, -60, 60) + clamp(slope * 120, -28, 28)
        scores.append(weighted_tf_score(tf, score))
    return weighted_average(scores)


def momentum_score(tf_bundles: dict[str, SeriesBundle]) -> Optional[float]:
    scores = []
    for tf, bundle in tf_bundles.items():
        closes = bundle.closes
        if len(closes) < 15:
            continue
        score = 0.0
        score += pct_score(percent_change(closes, 3), 18)
        score += pct_score(percent_change(closes, 5), 14)
        score += pct_score(percent_change(closes, 10), 10)
        rsi_value = rsi(closes, 14)
        score += clamp((rsi_value - 50) * 1.15, -28, 28)
        body = closes[-1] - closes[-2]
        avg_move = sum(abs(closes[i] - closes[i - 1]) for i in range(max(1, len(closes) - 12), len(closes))) / 12
        if avg_move:
            score += clamp(body / avg_move * 12, -18, 18)
        scores.append(weighted_tf_score(tf, score))
    return weighted_average(scores)


def volatility_score(tf_bundles: dict[str, SeriesBundle]) -> Optional[float]:
    bundle = tf_bundles.get("H1") or tf_bundles.get("M15") or next(iter(tf_bundles.values()), None)
    if not bundle or len(bundle.closes) < 40:
        return None
    atr_now = atr(bundle, 14)
    atr_values = rolling_atr(bundle, 14, 40)
    if not atr_values or not atr_now:
        return 0.0
    percentile = sum(1 for value in atr_values if value <= atr_now) / len(atr_values)
    trend = 1 if bundle.closes[-1] >= ema(bundle.closes, min(20, len(bundle.closes))) else -1
    adx_proxy = trend_strength_proxy(bundle)
    if percentile < 0.20 or adx_proxy < 0.18:
        return 0.0
    if percentile > 0.88:
        return clamp(trend * 10, -10, 10)
    return clamp(trend * (percentile - 0.45) * 42, -18, 18)


def intermarket_score(symbol: str, market_data: dict[str, SeriesBundle], binance: dict) -> Optional[float]:
    def direction(key: str) -> Optional[float]:
        bundle = market_data.get(INTERMARKET_TICKERS[key])
        if not bundle or len(bundle.closes) < 6:
            return None
        return pct_score(percent_change(bundle.closes, 4), 22)

    scores: list[float] = []
    if symbol in {"NAS100", "SP500", "DJ30"}:
        for key in ("NASDAQ_F", "SP500_F", "DJ30_F"):
            value = direction(key)
            if value is not None:
                scores.append(value)
        for key, weight in (("VIX", -1.0), ("DXY", -0.35), ("US10Y", -0.25)):
            value = direction(key)
            if value is not None:
                scores.append(weight * value)
    elif symbol == "XAUUSD":
        for key, weight in (("DXY", -0.8), ("US10Y", -0.7), ("VIX", 0.25)):
            value = direction(key)
            if value is not None:
                scores.append(weight * value)
    elif symbol == "BTCUSD":
        for key, weight in (("ETHUSD", 0.45), ("NASDAQ_F", 0.35), ("DXY", -0.35)):
            value = direction(key)
            if value is not None:
                scores.append(weight * value)
        if binance.get("availability") != "unavailable":
            scores.append(float(binance.get("score") or 0))
    elif symbol == "GER40":
        for key, weight in (("EUROSTOXX", 0.55), ("EURUSD", 0.2), ("DXY", -0.25), ("SP500_F", 0.25)):
            value = direction(key)
            if value is not None:
                scores.append(weight * value)
    return weighted_average([(1.0, score) for score in scores])


def macro_news_score(macro: dict, news: dict) -> Optional[float]:
    scores = []
    if macro.get("availability") != "unavailable":
        scores.append(float(macro.get("score") or 0))
    if news.get("availability") != "unavailable":
        scores.append(float(news.get("score") or 0))
    return weighted_average([(1.0, score) for score in scores])


def live_probabilities(
    final_score: float,
    direction: str,
    data_quality: float,
    agreement: float,
    timeframe_alignment: float,
    volatility_state: str,
    macro_risk: str,
    source_availability: dict,
) -> tuple[int, int, int]:
    primary = 50 + max(1.0, min(35.0, abs(final_score) * 0.5))
    primary += max(0.0, agreement - 0.55) * 8
    primary += max(0.0, timeframe_alignment - 0.55) * 5
    if data_quality < 70:
        primary -= (70 - data_quality) * 0.12
    if volatility_state in {"CHOPPY", "COMPRESSED"}:
        primary -= 4
    elif volatility_state == "HIGH_VOLATILITY":
        primary -= 3
    if macro_risk == "HIGH":
        primary -= 5
    elif macro_risk == "UNKNOWN":
        primary -= 2
    if any(value == "unavailable" for value in source_availability.values()):
        primary -= 1
    if data_quality < 50:
        primary = min(primary, 56)
    max_cap = 90 if abs(final_score) >= 76 and data_quality >= 80 and agreement >= 0.78 else 85
    confidence = int(round(clamp(primary, 51, max_cap)))
    if direction == "LONG":
        return confidence, confidence, 100 - confidence
    return confidence, 100 - confidence, confidence


def confidence_strength(confidence: int, data_quality: float, agreement: float) -> str:
    if confidence >= 76 and data_quality >= 75 and agreement >= 0.70:
        return "VERY STRONG"
    if confidence >= 66:
        return "STRONG"
    if confidence >= 57:
        return "MEDIUM"
    return "WEAK"


def live_risk_flags(
    data_quality: float,
    macro_risk: str,
    volatility_state: str,
    agreement: float,
    source_availability: dict,
) -> tuple[str, list[str]]:
    flags: list[str] = []
    if data_quality < 50:
        flags.append("LOW_DATA_QUALITY")
    if macro_risk == "HIGH":
        flags.append("HIGH_MACRO_RISK")
    elif macro_risk == "UNKNOWN":
        flags.append("MACRO_UNKNOWN")
    if volatility_state in {"CHOPPY", "COMPRESSED"}:
        flags.append("CHOPPY_MARKET")
    elif volatility_state == "HIGH_VOLATILITY":
        flags.append("HIGH_VOLATILITY")
    if agreement < 0.50:
        flags.append("MIXED_SIGNALS")
    if all(value == "unavailable" for value in source_availability.values()):
        flags.append("ALL_SOURCES_UNAVAILABLE")
    if data_quality < 50 or macro_risk == "HIGH" or volatility_state == "CHOPPY":
        return "HIGH", flags
    if flags or data_quality < 70 or agreement < 0.62:
        return "MEDIUM", flags
    return "LOW", flags


def live_reasons(
    symbol: str,
    direction: str,
    tf_bundles: dict[str, SeriesBundle],
    factors: dict[str, Optional[float]],
    macro: dict,
    news: dict,
    binance: dict,
    volatility_state: str,
    flags: list[str],
) -> list[str]:
    reasons: list[str] = []
    h1 = tf_bundles.get("H1") or next(iter(tf_bundles.values()), None)
    if h1 and len(h1.closes) >= 20:
        vwap = approximate_vwap(h1, min(len(h1.closes), 48))
        if vwap:
            reasons.append(f"H1 {'above' if h1.closes[-1] >= vwap else 'below'} VWAP")
        ema20 = ema(h1.closes, 20)
        ema50 = ema(h1.closes, min(50, len(h1.closes)))
        reasons.append(f"EMA20 {'above' if ema20 >= ema50 else 'below'} EMA50")
        rsi_value = rsi(h1.closes, 14)
        reasons.append("momentum positive" if rsi_value >= 50 else "momentum negative")
    if abs(float(factors.get("market_structure") or 0)) < 10 or abs(float(factors.get("momentum") or 0)) < 10:
        reasons.append("mixed signals / low momentum")
    if symbol in {"NAS100", "SP500", "DJ30"} and factors.get("intermarket") is not None:
        reasons.append("VIX/DXY/yields proxy supports " + direction.lower())
    if symbol == "BTCUSD" and binance.get("availability") != "unavailable":
        reasons.extend(str(reason) for reason in (binance.get("reasons") or [])[:1])
    if macro.get("availability") == "unavailable":
        reasons.append("macro risk unknown")
    elif macro.get("risk") == "HIGH":
        reasons.append("high impact macro risk")
    if news.get("availability") == "unavailable":
        reasons.append("news source unavailable")
    if volatility_state in {"CHOPPY", "COMPRESSED"}:
        reasons.append("choppy/compressed volatility")
    if "LOW_DATA_QUALITY" in flags:
        reasons.append("low data quality")
    if not reasons:
        reasons.append(f"{direction.lower()} score {round(float(factors.get('trend') or 0))}")
    return reasons[:6]


def invalidation_info(direction: str, tf_bundles: dict[str, SeriesBundle]) -> dict:
    bundle = tf_bundles.get("H1") or tf_bundles.get("M15") or next(iter(tf_bundles.values()), None)
    if not bundle or len(bundle.closes) < 12:
        return {"level": None, "condition": "insufficient market data"}
    if direction == "LONG":
        level = min(bundle.lows[-12:])
        return {"level": round(level, 5), "condition": "H1 close below recent swing low"}
    level = max(bundle.highs[-12:])
    return {"level": round(level, 5), "condition": "H1 close above recent swing high"}


def symbol_source_availability(symbol: str, tf_bundles: dict[str, SeriesBundle], context: dict, factors: dict) -> dict:
    tf_availability = {
        tf: "available" if tf in tf_bundles else "unavailable"
        for tf in LIVE_TIMEFRAMES
    }
    factor_availability = {
        key: "available" if value is not None else "unavailable"
        for key, value in factors.items()
    }
    global_sources = context.get("source_availability") or {}
    return {
        **{f"timeframe_{key.lower()}": value for key, value in tf_availability.items()},
        **{f"factor_{key}": value for key, value in factor_availability.items()},
        "market_data": global_sources.get("market_data", "unavailable"),
        "intermarket": global_sources.get("intermarket", "unavailable"),
        "macro_calendar": global_sources.get("macro_calendar", "unavailable"),
        "news_sentiment": global_sources.get("news_sentiment", "unavailable"),
        "binance_derivatives": global_sources.get("binance_derivatives", "unavailable"),
    }


def live_data_quality_score(
    source_availability: dict,
    available_factors: dict,
    tf_bundles: dict[str, SeriesBundle],
    macro: dict,
    news: dict,
    binance: dict,
) -> float:
    factor_quality = len(available_factors) / len(LIVE_FACTOR_WEIGHTS) * 42
    tf_quality = sum(LIVE_TIMEFRAMES[tf]["weight"] for tf in tf_bundles if tf in LIVE_TIMEFRAMES) * 34
    source_quality = 0.0
    for key in ("market_data", "intermarket", "macro_calendar", "news_sentiment", "binance_derivatives"):
        status = source_availability.get(key, "unavailable")
        source_quality += {"available": 4.8, "partial": 2.6}.get(status, 0.0)
    if macro.get("availability") != "unavailable":
        source_quality += min(4.0, float(macro.get("confidence") or 0) / 25)
    if news.get("availability") != "unavailable":
        source_quality += min(3.0, float(news.get("confidence") or 0) / 30)
    if binance.get("availability") == "available":
        source_quality += 2.0
    return round(clamp(factor_quality + tf_quality + source_quality, 0, 100), 1)


def aggregate_risk(rows: list[dict]) -> str:
    if any(row.get("risk") == "HIGH" for row in rows):
        return "HIGH"
    if any(row.get("risk") == "MEDIUM" for row in rows):
        return "MEDIUM"
    return "LOW" if rows else "UNKNOWN"


def volatility_regime(tf_bundles: dict[str, SeriesBundle]) -> str:
    bundle = tf_bundles.get("H1") or tf_bundles.get("M15") or next(iter(tf_bundles.values()), None)
    if not bundle or len(bundle.closes) < 40:
        return "UNKNOWN"
    atr_now = atr(bundle, 14)
    atr_values = rolling_atr(bundle, 14, 40)
    if not atr_now or not atr_values:
        return "UNKNOWN"
    percentile = sum(1 for value in atr_values if value <= atr_now) / len(atr_values)
    trend = trend_strength_proxy(bundle)
    if trend < 0.16:
        return "CHOPPY"
    if percentile < 0.20:
        return "COMPRESSED"
    if percentile > 0.90:
        return "HIGH_VOLATILITY"
    return "NORMAL"


def factor_agreement(scores: list[float], final_score: float) -> float:
    clean = [score for score in scores if score is not None and math.isfinite(score)]
    if not clean:
        return 0.0
    sign = 1 if final_score >= 0 else -1
    aligned = sum(1 for score in clean if score == 0 or score * sign >= 0)
    return aligned / len(clean)


def timeframe_trend_alignment(tf_bundles: dict[str, SeriesBundle]) -> float:
    directions = []
    for bundle in tf_bundles.values():
        if len(bundle.closes) >= 20:
            directions.append(1 if bundle.closes[-1] >= ema(bundle.closes, min(20, len(bundle.closes))) else -1)
    if not directions:
        return 0.0
    pos = directions.count(1)
    neg = directions.count(-1)
    return max(pos, neg) / len(directions)


def higher_highs_lows(highs: list[float], lows: list[float]) -> int:
    if len(highs) < 18 or len(lows) < 18:
        return 0
    recent_high = max(highs[-8:])
    previous_high = max(highs[-16:-8])
    recent_low = min(lows[-8:])
    previous_low = min(lows[-16:-8])
    if recent_high > previous_high and recent_low > previous_low:
        return 1
    if recent_high < previous_high and recent_low < previous_low:
        return -1
    return 0


def weighted_tf_score(tf: str, score: float) -> tuple[float, float]:
    return LIVE_TIMEFRAMES.get(tf, {}).get("weight", 0.10), clamp(score, -100, 100)


def weighted_average(weighted_scores: list[tuple[float, float]]) -> Optional[float]:
    clean = [(weight, score) for weight, score in weighted_scores if score is not None and math.isfinite(score)]
    if not clean:
        return None
    weight_sum = sum(weight for weight, _ in clean) or 1.0
    return clamp(sum(weight * score for weight, score in clean) / weight_sum, -100, 100)


def approximate_vwap(bundle: SeriesBundle, length: int) -> Optional[float]:
    highs = bundle.highs[-length:]
    lows = bundle.lows[-length:]
    closes = bundle.closes[-length:]
    volumes = bundle.volumes[-length:] if bundle.volumes else [1.0] * len(closes)
    if not closes:
        return None
    total_volume = sum(volume if volume > 0 else 1.0 for volume in volumes) or len(closes)
    total = 0.0
    for high, low, close, volume in zip(highs, lows, closes, volumes):
        typical = (high + low + close) / 3
        total += typical * (volume if volume > 0 else 1.0)
    return total / total_volume


def vwap_slope(bundle: SeriesBundle) -> float:
    if len(bundle.closes) < 20:
        return 0.0
    recent = approximate_vwap(bundle, min(len(bundle.closes), 20))
    previous_bundle = SeriesBundle(
        closes=bundle.closes[:-5],
        highs=bundle.highs[:-5],
        lows=bundle.lows[:-5],
        volumes=bundle.volumes[:-5],
        source=bundle.source,
    )
    previous = approximate_vwap(previous_bundle, min(len(previous_bundle.closes), 20))
    if not recent or not previous:
        return 0.0
    return (recent - previous) / previous * 100


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, min(period, len(values)))
    alpha = 2 / (period + 1)
    value = values[-period]
    for current in values[-period + 1:]:
        value = current * alpha + value * (1 - alpha)
    return float(value)


def ema_slope(values: list[float], period: int) -> float:
    if len(values) < period + 6:
        return 0.0
    now = ema(values, period)
    previous = ema(values[:-5], period)
    return ((now - previous) / previous * 100) if previous else 0.0


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains, losses = [], []
    for prev, cur in zip(values[-period - 1:-1], values[-period:]):
        delta = cur - prev
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 70.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(bundle: SeriesBundle, period: int = 14) -> float:
    if len(bundle.closes) <= period:
        return 0.0
    values = []
    for idx in range(len(bundle.closes) - period, len(bundle.closes)):
        prev_close = bundle.closes[idx - 1]
        values.append(max(bundle.highs[idx] - bundle.lows[idx], abs(bundle.highs[idx] - prev_close), abs(bundle.lows[idx] - prev_close)))
    return sum(values) / len(values) if values else 0.0


def rolling_atr(bundle: SeriesBundle, period: int, length: int) -> list[float]:
    values = []
    start = max(period + 1, len(bundle.closes) - length)
    for end in range(start, len(bundle.closes) + 1):
        window = SeriesBundle(
            closes=bundle.closes[:end],
            highs=bundle.highs[:end],
            lows=bundle.lows[:end],
            volumes=bundle.volumes[:end],
            source=bundle.source,
        )
        value = atr(window, period)
        if value:
            values.append(value)
    return values


def trend_strength_proxy(bundle: SeriesBundle) -> float:
    value = atr(bundle, 14)
    if not value or len(bundle.closes) < 20:
        return 0.0
    move = abs(bundle.closes[-1] - bundle.closes[-14])
    return clamp(move / (value * 14), 0, 1)


def percent_change(values: list[float], lookback: int) -> float:
    if len(values) <= lookback or values[-lookback] == 0:
        return 0.0
    return (values[-1] - values[-lookback]) / values[-lookback] * 100


def pct_score(value: float, scale: float) -> float:
    return clamp(value * scale, -100, 100)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _filtered_symbols(symbol: str | None) -> list[dict]:
    if not symbol:
        return BIAS_SYMBOLS
    requested = symbol.upper()
    aliases = {"US500": "SP500", "GER40FT": "GER40"}
    requested = aliases.get(requested, requested)
    return [item for item in BIAS_SYMBOLS if item["symbol"] == requested] or BIAS_SYMBOLS


def _short_time(value: object) -> str:
    if not value:
        return datetime.now(BERLIN_TZ).strftime("%H:%M")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(BERLIN_TZ).strftime("%H:%M")
    except Exception:
        return str(value)[:5]
