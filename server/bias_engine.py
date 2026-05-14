from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


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


@dataclass
class SeriesBundle:
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]


def calculate_bias_report(allow_network: bool = True, now: Optional[datetime] = None) -> dict:
    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    ny_now = now_utc.astimezone(NY_TZ)
    berlin_now = now_utc.astimezone(BERLIN_TZ)
    market_data, availability = _load_market_data(allow_network)
    macro = _macro_context(ny_now)
    news = _news_context()

    rows = []
    qualities = []
    for item in BIAS_SYMBOLS:
        result = _symbol_bias(item, market_data, macro, news)
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
        "source_availability": availability | {"macro_calendar": macro["available"], "news_sentiment": news["available"]},
        "symbols": rows,
    }
    report["telegram_text"] = format_bias_telegram_message(report)
    return report


def format_bias_telegram_message(report: dict) -> str:
    lines = [
        "📊 NY PRE-MARKET BIAS",
        f"🕒 {report.get('ny_time', '09:20 NY')} / {report.get('berlin_time', '15:20 DE')}",
        "",
    ]
    for row in report.get("symbols", []):
        lines.append(f"{row['symbol']}: {row['bias']} {row['confidence']}%")
    lines.extend(["", f"Macro Risk: {report.get('macro_risk', 'UNKNOWN')}", ""])
    for row in report.get("symbols", []):
        reason = "; ".join(row.get("reasons", [])[:3]) or "data limited"
        lines.append(f"{row['symbol']}: {reason}")
    unavailable = [k for k, v in (report.get("source_availability") or {}).items() if not v]
    if unavailable:
        lines.extend(["", "Data unavailable: " + ", ".join(unavailable[:6])])
    return "\n".join(lines)


def _load_market_data(allow_network: bool) -> tuple[dict[str, SeriesBundle], dict[str, bool]]:
    tickers = {item["ticker"] for item in BIAS_SYMBOLS} | set(INTERMARKET_TICKERS.values())
    data: dict[str, SeriesBundle] = {}
    availability: dict[str, bool] = {"market_data": False, "intermarket": False}
    if not allow_network:
        return data, availability
    try:
        import yfinance as yf
    except Exception:
        return data, availability

    for ticker in sorted(tickers):
        try:
            frame = yf.download(ticker, period="10d", interval="1h", progress=False, auto_adjust=False, threads=False)
        except Exception:
            continue
        bundle = _bundle_from_frame(frame)
        if bundle and len(bundle.closes) >= 20:
            data[ticker] = bundle
    availability["market_data"] = any(item["ticker"] in data for item in BIAS_SYMBOLS)
    availability["intermarket"] = any(ticker in data for ticker in INTERMARKET_TICKERS.values())
    return data, availability


def _bundle_from_frame(frame: Any) -> Optional[SeriesBundle]:
    if frame is None or getattr(frame, "empty", True):
        return None

    def column(name: str) -> list[float]:
        try:
            raw = frame[name]
            if hasattr(raw, "iloc") and hasattr(raw, "columns"):
                raw = raw.iloc[:, 0]
            values = raw.dropna().astype(float).tolist()
            return [float(v) for v in values if math.isfinite(float(v))]
        except Exception:
            return []

    closes = column("Close")
    highs = column("High") or closes
    lows = column("Low") or closes
    volumes = column("Volume")
    if not closes:
        return None
    if len(highs) != len(closes):
        highs = closes[:]
    if len(lows) != len(closes):
        lows = closes[:]
    if len(volumes) != len(closes):
        volumes = [1.0] * len(closes)
    return SeriesBundle(closes=closes, highs=highs, lows=lows, volumes=volumes)


def _symbol_bias(item: dict, market_data: dict[str, SeriesBundle], macro: dict, news: dict) -> dict:
    bundle = market_data.get(item["ticker"])
    sources = {"market_structure": bool(bundle), "premarket": bool(bundle), "intermarket": bool(market_data), "macro_calendar": macro["available"], "news_sentiment": news["available"]}
    factors = {
        "market_structure": _market_structure_score(bundle),
        "premarket": _premarket_score(bundle),
        "intermarket": _intermarket_score(item["symbol"], market_data),
        "macro_calendar": macro["score"],
        "news_sentiment": news["score"],
        "volatility_regime": _volatility_regime_score(bundle),
    }
    available = {k: v for k, v in factors.items() if v is not None}
    if not available:
        final = 0.0
    else:
        total_weight = sum(WEIGHTS[k] for k in available)
        final = sum(score * WEIGHTS[k] for k, score in available.items()) / total_weight
    conflict = _factor_conflict(list(available.values()))
    data_quality = round(100 * len(available) / len(WEIGHTS), 1)
    confidence = _confidence(final, data_quality, conflict, macro["risk"])
    bias = _bias_label(final, confidence, conflict, macro["risk"])
    long_pct, short_pct, consolidation_pct = _percentages(final, confidence, bias)
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
        "high_risk": macro["risk"] == "HIGH",
        "reasons": _reasons(item["symbol"], bundle, market_data, final, bias, macro, news),
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


def _intermarket_score(symbol: str, market_data: dict[str, SeriesBundle]) -> Optional[float]:
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


def _macro_context(now_ny: datetime) -> dict:
    # No confirmed calendar adapter is configured in this project. Mark common
    # high-impact NY morning windows as caution only; do not invent events.
    weekday = now_ny.weekday()
    hour = now_ny.hour + now_ny.minute / 60
    risk = "MEDIUM"
    score = 0.0
    if weekday >= 5:
        risk = "HIGH"
    elif 8.0 <= hour <= 10.2:
        risk = "MEDIUM"
    return {"available": False, "risk": risk, "score": score}


def _news_context() -> dict:
    return {"available": False, "score": 0.0}


def _macro_risk(rows: list[dict], macro: dict) -> str:
    if macro["risk"] == "HIGH" or any(row.get("high_risk") for row in rows):
        return "HIGH"
    low_quality = sum(1 for row in rows if row.get("data_quality_score", 0) < 55)
    if low_quality >= max(1, len(rows) // 2):
        return "MEDIUM"
    return "LOW" if macro["available"] else "MEDIUM"


def _reasons(symbol: str, bundle: Optional[SeriesBundle], market_data: dict[str, SeriesBundle], score: float, bias: str, macro: dict, news: dict) -> list[str]:
    reasons: list[str] = []
    if bundle:
        relation = "above" if bundle.closes[-1] > _ema(bundle.closes, 20) else "below"
        reasons.append(f"H1 price {relation} EMA20")
        momentum = "up" if _momentum_score(bundle.closes, 4, 1) >= 0 else "down"
        reasons.append(f"last hours momentum {momentum}")
    if symbol in {"NAS100", "SP500", "DJ30"}:
        vix = _intermarket_direction_text("VIX", market_data, inverse=True)
        if vix:
            reasons.append(vix)
    if symbol == "XAUUSD":
        dxy = _intermarket_direction_text("DXY", market_data, inverse=True, label="DXY")
        if dxy:
            reasons.append(dxy)
    if symbol == "BTCUSD":
        eth = _intermarket_direction_text("ETHUSD", market_data, label="ETH")
        if eth:
            reasons.append(eth)
    if macro["risk"] != "LOW":
        reasons.append("macro calendar unconfirmed")
    if not news["available"]:
        reasons.append("news source unavailable")
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
    confidence *= max(0.45, quality / 100)
    if conflict:
        confidence -= 12
    if macro_risk == "HIGH":
        confidence -= 10
    return int(round(_clamp(confidence, 0, 88)))


def _bias_label(score: float, confidence: int, conflict: bool, macro_risk: str) -> str:
    if confidence < 57 or conflict or macro_risk == "HIGH" or abs(score) < 18:
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

