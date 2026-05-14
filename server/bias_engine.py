from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .bias_sources import SeriesBundle, load_bias_source_context


NY_TZ = ZoneInfo("America/New_York")
BERLIN_TZ = ZoneInfo("Europe/Berlin")

BIAS_SYMBOLS = [
    {"symbol": "NAS100", "bot_id": "NAS100_ORB_VWAP_RSI_OF", "ticker": "NQ=F"},
    {"symbol": "SP500", "bot_id": "SP500_ORB_VWAP_RSI_OF", "ticker": "ES=F"},
    {"symbol": "DJ30", "bot_id": "DJ30_ORB_VWAP_RSI_OF", "ticker": "YM=F"},
    {"symbol": "XAUUSD", "bot_id": "XAUUSD", "ticker": "GC=F"},
    {"symbol": "BTCUSD", "bot_id": "BTCUSD_ORB_VWAP_RSI_OF", "ticker": "BTC-USD"},
    {"symbol": "GER40", "bot_id": "GER40_ORB_VWAP_RSI_OF", "ticker": "^GDAXI"},
]

INTERMARKET_TICKERS = {
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "EURUSD": "EURUSD=X",
    "ETHUSD": "ETH-USD",
    "NASDAQ_F": "NQ=F",
    "SP500_F": "ES=F",
    "DJ30_F": "YM=F",
    "EUROSTOXX": "^STOXX50E",
}

WEIGHTS = {
    "market_structure": 0.30,
    "premarket": 0.15,
    "intermarket": 0.20,
    "macro_calendar": 0.15,
    "news_sentiment": 0.10,
    "volatility_regime": 0.10,
}


def calculate_bias_report(allow_network: bool = True, now: Optional[datetime] = None) -> dict:
    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    ny_now = now_utc.astimezone(NY_TZ)
    berlin_now = now_utc.astimezone(BERLIN_TZ)
    source_context = load_bias_source_context(BIAS_SYMBOLS, INTERMARKET_TICKERS, allow_network=allow_network)
    market_data = source_context["market_data"]
    macro = source_context["macro"]
    news = source_context["news"]
    binance = source_context["binance"]

    rows = []
    qualities = []
    for item in BIAS_SYMBOLS:
        result = _symbol_bias(item, market_data, macro, news, binance)
        rows.append(result)
        qualities.append(result["data_quality_score"])

    macro_risk = _macro_risk(rows, macro)
    quality = round(sum(qualities) / len(qualities), 1) if qualities else 0
    report = {
        "report_date": ny_now.strftime("%Y-%m-%d"),
        "run_at": now_utc.isoformat(),
        "ny_time": ny_now.strftime("%H:%M %Z"),
        "berlin_time": berlin_now.strftime("%H:%M %Z"),
        "macro_risk": macro_risk,
        "data_quality_score": quality,
        "source_availability": source_context["source_availability"],
        "source_details": source_context.get("source_details") or {},
        "symbols": rows,
    }
    report["telegram_text"] = format_bias_telegram_message(report)
    return report


def format_bias_telegram_message(report: dict) -> str:
    lines = [
        "\U0001f4ca NY PRE-MARKET BIAS",
        f"\U0001f552 {report.get('ny_time', '09:20 NY')} / {report.get('berlin_time', '15:20 DE')}",
        "",
    ]
    for row in report.get("symbols", []):
        lines.append(f"{row['symbol']}: {row['bias']} {row['confidence']}%")
    lines.extend(
        [
            "",
            f"Macro Risk: {report.get('macro_risk', 'UNKNOWN')}",
            f"\U0001f4e1 Data Quality: {int(round(float(report.get('data_quality_score') or 0)))}%",
            "",
            "Reasons:",
        ]
    )
    for row in report.get("symbols", []):
        reason = "; ".join(row.get("reasons", [])[:3]) or "data limited"
        lines.append(f"{row['symbol']}: {reason}")
    unavailable = [k for k, v in (report.get("source_availability") or {}).items() if v == "unavailable"]
    if unavailable:
        lines.extend(["", "Data unavailable: " + ", ".join(unavailable[:6])])
    if float(report.get("data_quality_score") or 0) < 50:
        lines.extend(["", "\u26a0 LOW DATA QUALITY  bias is limited."])
    return "\n".join(lines)


def _symbol_bias(item: dict, market_data: dict[str, SeriesBundle], macro: dict, news: dict, binance: dict) -> dict:
    bundle = market_data.get(item["ticker"])
    intermarket_score = _intermarket_score(item["symbol"], market_data, binance)
    factors = {
        "market_structure": _market_structure_score(bundle),
        "premarket": _premarket_score(bundle),
        "intermarket": intermarket_score,
        "macro_calendar": _source_score(macro),
        "news_sentiment": _source_score(news),
        "volatility_regime": _volatility_regime_score(bundle),
    }
    sources = {
        "market_structure": "available" if factors["market_structure"] is not None else "unavailable",
        "premarket": "available" if factors["premarket"] is not None else "unavailable",
        "intermarket": _symbol_intermarket_status(item["symbol"], market_data, binance),
        "macro_calendar": macro.get("availability", "unavailable"),
        "news_sentiment": news.get("availability", "unavailable"),
        "volatility_regime": "available" if factors["volatility_regime"] is not None else "unavailable",
    }
    available = {k: v for k, v in factors.items() if v is not None}
    if not available:
        final = 0.0
    else:
        total_weight = sum(WEIGHTS[k] for k in available)
        final = sum(score * WEIGHTS[k] for k, score in available.items()) / total_weight

    conflict = _factor_conflict(list(available.values())) or _strong_structure_intermarket_conflict(
        factors["market_structure"], factors["intermarket"]
    )
    data_quality = _data_quality_score(sources, macro, news, binance)
    macro_risk = macro.get("risk", "UNKNOWN")
    confidence = _confidence(final, data_quality, conflict, macro_risk)
    bias = _bias_label(final, confidence, conflict, macro_risk, data_quality)
    long_pct, short_pct, consolidation_pct = _percentages(final, confidence, bias)

    flags = []
    if data_quality < 50:
        flags.append("LOW_DATA_QUALITY")
    if conflict:
        flags.append("FACTOR_CONFLICT")
    if macro_risk in {"HIGH", "UNKNOWN"}:
        flags.append(f"MACRO_{macro_risk}")

    return {
        "symbol": item["symbol"],
        "bot_id": item["bot_id"],
        "bias": bias,
        "score": round(final, 1),
        "confidence": confidence,
        "long_percent": long_pct,
        "short_percent": short_pct,
        "consolidation_percent": consolidation_pct,
        "factor_scores": {k: round(v, 1) if v is not None else None for k, v in factors.items()},
        "source_availability": sources,
        "data_quality_score": data_quality,
        "high_risk": macro_risk == "HIGH",
        "flags": flags,
        "reasons": _reasons(item["symbol"], bundle, market_data, final, bias, macro, news, binance),
    }


def _market_structure_score(bundle: Optional[SeriesBundle]) -> Optional[float]:
    if not bundle or len(bundle.closes) < 30:
        return None
    closes = bundle.closes
    score = 0.0
    for period, weight in ((20, 22), (50, 26), (200, 16)):
        ema = _ema(closes, min(period, len(closes)))
        score += weight if closes[-1] > ema else -weight
    score += _momentum_score(closes, 6, 18)
    score += _rsi_score(_rsi(closes, 14))
    score += _hh_ll_score(bundle.highs, bundle.lows)
    return _clamp(score, -100, 100)


def _premarket_score(bundle: Optional[SeriesBundle]) -> Optional[float]:
    if not bundle or len(bundle.closes) < 10:
        return None
    closes = bundle.closes
    last = closes[-1]
    score = _pct_score((last - closes[-4]) / closes[-4] * 100, 55)
    score += _pct_score((last - closes[-8]) / closes[-8] * 100, 35)
    ranges = [abs(h - l) for h, l in zip(bundle.highs[-8:], bundle.lows[-8:])]
    base_ranges = [abs(h - l) for h, l in zip(bundle.highs[-30:-8], bundle.lows[-30:-8])]
    if ranges and base_ranges:
        expansion = (sum(ranges) / len(ranges)) / (sum(base_ranges) / len(base_ranges) or 1)
        score += 10 if expansion > 1.15 else -8 if expansion < 0.75 else 0
    return _clamp(score, -100, 100)


def _intermarket_score(symbol: str, market_data: dict[str, SeriesBundle], binance: dict) -> Optional[float]:
    def direction(key: str) -> Optional[float]:
        bundle = market_data.get(INTERMARKET_TICKERS[key])
        return _momentum_score(bundle.closes, 4, 25) if bundle else None

    scores: list[float] = []
    if symbol in {"NAS100", "SP500", "DJ30"}:
        for key in ("NASDAQ_F", "SP500_F", "DJ30_F"):
            value = direction(key)
            if value is not None:
                scores.append(value)
        vix = direction("VIX")
        dxy = direction("DXY")
        us10y = direction("US10Y")
        if vix is not None:
            scores.append(-vix)
        if dxy is not None:
            scores.append(-0.35 * dxy)
        if us10y is not None:
            scores.append(-0.25 * us10y)
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
    return _average(scores)


def _volatility_regime_score(bundle: Optional[SeriesBundle]) -> Optional[float]:
    if not bundle or len(bundle.closes) < 40:
        return None
    adx = _adx(bundle.highs, bundle.lows, bundle.closes, 14)
    atr = _atr(bundle.highs, bundle.lows, bundle.closes, 14)
    price = bundle.closes[-1] or 1
    atr_pct = atr / price * 100 if atr else 0
    if adx < 17 or atr_pct < 0.18:
        return 0.0
    if adx > 25 and atr_pct > 0.25:
        return 18 if bundle.closes[-1] > _ema(bundle.closes, 20) else -18
    return 0.0


def _macro_risk(rows: list[dict], macro: dict) -> str:
    risk = macro.get("risk", "UNKNOWN")
    if risk == "HIGH" or any(row.get("high_risk") for row in rows):
        return "HIGH"
    if risk in {"LOW", "MEDIUM"} and macro.get("availability") != "unavailable":
        return risk
    return "UNKNOWN"


def _reasons(
    symbol: str,
    bundle: Optional[SeriesBundle],
    market_data: dict[str, SeriesBundle],
    score: float,
    bias: str,
    macro: dict,
    news: dict,
    binance: dict,
) -> list[str]:
    reasons: list[str] = []
    if bundle:
        relation = "above" if bundle.closes[-1] > _ema(bundle.closes, 20) else "below"
        reasons.append(f"H1 price {relation} EMA20")
        momentum = "up" if _momentum_score(bundle.closes, 4, 1) >= 0 else "down"
        reasons.append(f"last hours momentum {momentum}")
    if symbol in {"NAS100", "SP500", "DJ30"}:
        for key in ("VIX", "DXY", "US10Y"):
            reason = _intermarket_direction_text(key, market_data, inverse=True)
            if reason:
                reasons.append(reason)
                break
    if symbol == "XAUUSD":
        dxy = _intermarket_direction_text("DXY", market_data, inverse=True, label="DXY")
        if dxy:
            reasons.append(dxy)
    if symbol == "BTCUSD":
        eth = _intermarket_direction_text("ETHUSD", market_data, label="ETH")
        if eth:
            reasons.append(eth)
        reasons.extend(str(reason) for reason in binance.get("reasons", [])[:1])
    if symbol == "GER40":
        estoxx = _intermarket_direction_text("EUROSTOXX", market_data, label="EuroStoxx")
        if estoxx:
            reasons.append(estoxx)
    if macro.get("availability") == "unavailable":
        reasons.append("macro calendar unavailable")
    elif macro.get("risk") != "LOW":
        reasons.extend(str(reason) for reason in macro.get("reasons", [])[:1])
    if news.get("availability") == "unavailable":
        reasons.append("news source unavailable")
    else:
        reasons.extend(str(reason) for reason in news.get("reasons", [])[:1])
    if not reasons:
        reasons.append(f"{bias.lower()} score {round(score)}")
    return reasons[:4]


def _intermarket_direction_text(key: str, market_data: dict[str, SeriesBundle], inverse: bool = False, label: Optional[str] = None) -> Optional[str]:
    bundle = market_data.get(INTERMARKET_TICKERS[key])
    if not bundle:
        return None
    score = _momentum_score(bundle.closes, 4, 1)
    direction = "down" if score < 0 else "up"
    if inverse:
        impact = "supports risk" if score < 0 else "pressures risk"
        return f"{label or key} {direction}, {impact}"
    return f"{label or key} {direction}"


def _source_score(source: dict) -> Optional[float]:
    if source.get("availability") == "unavailable":
        return None
    return float(source.get("score") or 0)


def _symbol_intermarket_status(symbol: str, market_data: dict[str, SeriesBundle], binance: dict) -> str:
    keys = {
        "NAS100": ("NASDAQ_F", "SP500_F", "DJ30_F", "VIX", "DXY", "US10Y"),
        "SP500": ("NASDAQ_F", "SP500_F", "DJ30_F", "VIX", "DXY", "US10Y"),
        "DJ30": ("NASDAQ_F", "SP500_F", "DJ30_F", "VIX", "DXY", "US10Y"),
        "XAUUSD": ("DXY", "US10Y", "VIX"),
        "BTCUSD": ("ETHUSD", "NASDAQ_F", "DXY"),
        "GER40": ("EUROSTOXX", "EURUSD", "DXY", "SP500_F"),
    }.get(symbol, ())
    available = sum(1 for key in keys if market_data.get(INTERMARKET_TICKERS[key]))
    total = len(keys)
    if symbol == "BTCUSD":
        total += 1
        available += 1 if binance.get("availability") != "unavailable" else 0
    if available <= 0:
        return "unavailable"
    if available >= total:
        return "available"
    return "partial"


def _data_quality_score(sources: dict[str, str], macro: dict, news: dict, binance: dict) -> float:
    confidence = {
        "market_structure": 72,
        "premarket": 64,
        "intermarket": 66,
        "macro_calendar": float(macro.get("confidence") or 0),
        "news_sentiment": float(news.get("confidence") or 0),
        "volatility_regime": 55,
    }
    weighted = 0.0
    for key, weight in WEIGHTS.items():
        weighted += weight * _availability_weight(sources.get(key, "unavailable")) * confidence[key]
    if binance.get("availability") == "partial":
        weighted += 2
    elif binance.get("availability") == "available":
        weighted += 4
    return round(_clamp(weighted, 0, 100), 1)


def _availability_weight(status: str) -> float:
    if status == "available":
        return 1.0
    if status == "partial":
        return 0.55
    return 0.0


def _strong_structure_intermarket_conflict(structure: Optional[float], intermarket: Optional[float]) -> bool:
    if structure is None or intermarket is None:
        return False
    return abs(structure) >= 30 and abs(intermarket) >= 30 and structure * intermarket < 0


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, min(period, len(values)))
    alpha = 2 / (period + 1)
    ema = values[-period]
    for value in values[-period + 1:]:
        ema = value * alpha + ema * (1 - alpha)
    return float(ema)


def _rsi(values: list[float], period: int = 14) -> float:
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


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 0.0
    trs = []
    for idx in range(len(closes) - period, len(closes)):
        prev_close = closes[idx - 1]
        trs.append(max(highs[idx] - lows[idx], abs(highs[idx] - prev_close), abs(lows[idx] - prev_close)))
    return sum(trs) / len(trs) if trs else 0.0


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    atr = _atr(highs, lows, closes, period)
    if atr <= 0 or len(closes) <= period + 1:
        return 0.0
    moves = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes))]
    return _clamp((sum(moves) / len(moves)) / atr * 25, 0, 60)


def _momentum_score(values: list[float], lookback: int, scale: float) -> float:
    if len(values) <= lookback or values[-lookback] == 0:
        return 0.0
    pct = (values[-1] - values[-lookback]) / values[-lookback] * 100
    return _pct_score(pct, scale)


def _pct_score(pct: float, scale: float) -> float:
    return _clamp(pct * scale, -100, 100)


def _rsi_score(value: float) -> float:
    if value > 62:
        return 14
    if value < 38:
        return -14
    return (value - 50) * 0.7


def _hh_ll_score(highs: list[float], lows: list[float]) -> float:
    if len(highs) < 12 or len(lows) < 12:
        return 0.0
    hh = highs[-1] > max(highs[-12:-1])
    ll = lows[-1] < min(lows[-12:-1])
    if hh and not ll:
        return 12
    if ll and not hh:
        return -12
    return 0.0


def _factor_conflict(scores: list[float]) -> bool:
    strong_pos = sum(1 for score in scores if score >= 30)
    strong_neg = sum(1 for score in scores if score <= -30)
    return strong_pos > 0 and strong_neg > 0


def _confidence(score: float, quality: float, conflict: bool, macro_risk: str) -> int:
    confidence = min(82.0, 45.0 + abs(score) * 0.45)
    confidence *= max(0.25, quality / 100)
    if conflict:
        confidence -= 12
    if macro_risk == "HIGH":
        confidence -= 12
    elif macro_risk == "UNKNOWN":
        confidence -= 6
    return int(round(_clamp(confidence, 0, 88)))


def _bias_label(score: float, confidence: int, conflict: bool, macro_risk: str, data_quality: float) -> str:
    if confidence < 57 or data_quality < 50 or conflict or macro_risk == "HIGH" or abs(score) < 18:
        return "CONSOLIDATION"
    return "LONG" if score > 0 else "SHORT"


def _percentages(score: float, confidence: int, bias: str) -> tuple[int, int, int]:
    if bias == "CONSOLIDATION":
        con = max(confidence, 50)
        remainder = 100 - con
        long_pct = int(round(remainder * (0.5 + _clamp(score, -60, 60) / 120)))
        short_pct = 100 - con - long_pct
        return long_pct, short_pct, con
    primary = confidence
    consolidation = max(8, 100 - confidence)
    secondary = max(0, 100 - primary - consolidation)
    if bias == "LONG":
        return primary, secondary, consolidation
    return secondary, primary, consolidation


def _average(values: list[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if not clean:
        return None
    return _clamp(sum(clean) / len(clean), -100, 100)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
