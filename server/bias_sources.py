from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


HTTP_TIMEOUT_SECONDS = 8


@dataclass
class SeriesBundle:
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]
    source: str


def load_bias_source_context(symbol_items: list[dict], intermarket_tickers: dict[str, str], allow_network: bool) -> dict:
    if not allow_network:
        return _empty_context("network disabled")

    tickers = sorted({item["ticker"] for item in symbol_items} | set(intermarket_tickers.values()))
    market_data: dict[str, SeriesBundle] = {}
    source_names: dict[str, str] = {}
    for ticker in tickers:
        bundle = yahoo_chart_series(ticker) or yfinance_series(ticker)
        if bundle and len(bundle.closes) >= 20:
            market_data[ticker] = bundle
            source_names[ticker] = bundle.source

    symbol_tickers = {item["ticker"] for item in symbol_items}
    intermarket_values = set(intermarket_tickers.values())
    market_status = _availability_status(sum(1 for ticker in symbol_tickers if ticker in market_data), len(symbol_tickers))
    intermarket_status = _availability_status(sum(1 for ticker in intermarket_values if ticker in market_data), len(intermarket_values))
    binance = binance_btc_derivatives()
    macro = macro_calendar_context()
    news = news_sentiment_context()
    return {
        "market_data": market_data,
        "source_availability": {
            "market_data": market_status,
            "intermarket": intermarket_status,
            "macro_calendar": macro["availability"],
            "news_sentiment": news["availability"],
            "binance_derivatives": binance["availability"],
        },
        "source_details": {
            "market_data_sources": source_names,
            "macro_source": macro.get("raw_source_name"),
            "news_source": news.get("raw_source_name"),
            "binance_source": binance.get("raw_source_name"),
        },
        "macro": macro,
        "news": news,
        "binance": binance,
    }


def _empty_context(reason: str) -> dict:
    macro = {"availability": "unavailable", "risk": "UNKNOWN", "score": 0.0, "confidence": 0, "reasons": [reason], "raw_source_name": None}
    news = {"availability": "unavailable", "score": 0.0, "confidence": 0, "reasons": [reason], "headlines": [], "raw_source_name": None}
    binance = {"availability": "unavailable", "score": 0.0, "confidence": 0, "reasons": [reason], "raw_source_name": None}
    return {
        "market_data": {},
        "source_availability": {
            "market_data": "unavailable",
            "intermarket": "unavailable",
            "macro_calendar": "unavailable",
            "news_sentiment": "unavailable",
            "binance_derivatives": "unavailable",
        },
        "source_details": {},
        "macro": macro,
        "news": news,
        "binance": binance,
    }


def yahoo_chart_series(ticker: str, interval: str = "1h", range_value: str = "30d") -> Optional[SeriesBundle]:
    encoded = urllib.parse.quote(ticker, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_value}&interval={interval}"
    data = _fetch_json(url)
    try:
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        closes = _clean_numbers(quote.get("close") or [])
        highs = _clean_numbers(quote.get("high") or [])
        lows = _clean_numbers(quote.get("low") or [])
        volumes = _clean_numbers(quote.get("volume") or [])
    except (KeyError, IndexError, TypeError):
        return None
    if not closes:
        return None
    return _aligned_bundle(closes, highs, lows, volumes, "yahoo_chart")


def yfinance_series(ticker: str) -> Optional[SeriesBundle]:
    try:
        import yfinance as yf
        frame = yf.download(ticker, period="30d", interval="1h", progress=False, auto_adjust=False, threads=False)
    except Exception:
        return None
    if frame is None or getattr(frame, "empty", True):
        return None

    def column(name: str) -> list[float]:
        try:
            raw = frame[name]
            if hasattr(raw, "iloc") and hasattr(raw, "columns"):
                raw = raw.iloc[:, 0]
            return _clean_numbers(raw.dropna().astype(float).tolist())
        except Exception:
            return []

    return _aligned_bundle(column("Close"), column("High"), column("Low"), column("Volume"), "yfinance")


def binance_btc_derivatives() -> dict:
    premium = _fetch_json("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
    open_interest = _fetch_json("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT")
    reasons: list[str] = []
    scores: list[float] = []
    if premium:
        funding = _to_float(premium.get("lastFundingRate"))
        mark = _to_float(premium.get("markPrice"))
        index = _to_float(premium.get("indexPrice"))
        if funding is not None:
            scores.append(_clamp(-funding * 250000, -35, 35))
            reasons.append(f"BTC funding {funding:.4%}")
        if mark and index:
            premium_pct = (mark - index) / index * 100
            scores.append(_clamp(premium_pct * 20, -20, 20))
            reasons.append("BTC perp premium " + ("positive" if premium_pct >= 0 else "negative"))
    if open_interest:
        oi = _to_float(open_interest.get("openInterest"))
        if oi is not None:
            reasons.append("BTC open interest available")
    available_parts = int(bool(premium)) + int(bool(open_interest))
    return {
        "availability": _availability_status(available_parts, 2),
        "score": _average(scores) or 0.0,
        "confidence": 65 if available_parts == 2 else 40 if available_parts else 0,
        "reasons": reasons,
        "raw_source_name": "binance_public_futures" if available_parts else None,
    }


def macro_calendar_context() -> dict:
    url = os.getenv("BIAS_MACRO_CALENDAR_URL") or os.getenv("ECONOMIC_CALENDAR_API_URL")
    if not url:
        return {"availability": "unavailable", "risk": "UNKNOWN", "score": 0.0, "confidence": 0, "reasons": ["macro calendar source not configured"], "raw_source_name": None}
    headers = {}
    api_key = os.getenv("BIAS_MACRO_CALENDAR_KEY") or os.getenv("ECONOMIC_CALENDAR_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = _fetch_json(url, headers=headers)
    source_name = _safe_source_name(url)
    if not data:
        return {"availability": "unavailable", "risk": "UNKNOWN", "score": 0.0, "confidence": 0, "reasons": ["macro calendar request failed"], "raw_source_name": source_name}
    text = json.dumps(data, ensure_ascii=False).lower()
    high_terms = ("cpi", "fomc", "nfp", "nonfarm", "unemployment", "pmi", "gdp", "powell", "ecb")
    hits = [term.upper() for term in high_terms if term in text]
    risk = "HIGH" if hits else "MEDIUM"
    return {"availability": "available", "risk": risk, "score": 0.0, "confidence": 70, "reasons": [f"macro events: {', '.join(hits[:5])}" if hits else "macro source returned no high-impact keywords"], "raw_source_name": source_name}


def news_sentiment_context() -> dict:
    feeds = [
        ("marketwatch_top", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("yahoo_finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,QQQ,GC=F,BTC-USD&region=US&lang=en-US"),
    ]
    headlines: list[str] = []
    source_names: list[str] = []
    for name, url in feeds:
        items = _fetch_rss_titles(url, limit=5)
        if items:
            source_names.append(name)
            headlines.extend(items)
    if not headlines:
        return {"availability": "unavailable", "score": 0.0, "confidence": 0, "reasons": ["news RSS unavailable"], "headlines": [], "raw_source_name": None}

    negative_terms = ("selloff", "recession", "war", "inflation", "hawkish", "default", "crash", "risk-off")
    positive_terms = ("rally", "risk-on", "soft landing", "dovish", "record high", "optimism")
    text = " ".join(headlines).lower()
    neg = sum(text.count(term) for term in negative_terms)
    pos = sum(text.count(term) for term in positive_terms)
    score = _clamp((pos - neg) * 18, -60, 60)
    reasons = headlines[:3]
    return {"availability": "available", "score": score, "confidence": 55, "reasons": reasons, "headlines": headlines[:8], "raw_source_name": ",".join(source_names)}


def _fetch_json(url: str, headers: Optional[dict[str, str]] = None) -> Optional[dict]:
    try:
        safe_headers = {"User-Agent": "tv-smob-bias/1.0"}
        safe_headers.update(headers or {})
        request = urllib.request.Request(url, headers=safe_headers)
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _fetch_rss_titles(url: str, limit: int = 5) -> list[str]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "tv-smob-bias/1.0"})
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
        root = ET.fromstring(raw)
    except Exception:
        return []
    titles = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        if title:
            titles.append(" ".join(title.split())[:160])
        if len(titles) >= limit:
            break
    return titles


def _safe_source_name(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc or "configured_macro_url"
    except Exception:
        return "configured_macro_url"


def _aligned_bundle(closes: list[float], highs: list[float], lows: list[float], volumes: list[float], source: str) -> Optional[SeriesBundle]:
    if not closes:
        return None
    length = len(closes)
    highs = highs[-length:] if len(highs) >= length else closes[:]
    lows = lows[-length:] if len(lows) >= length else closes[:]
    volumes = volumes[-length:] if len(volumes) >= length else [1.0] * length
    return SeriesBundle(closes=closes[-length:], highs=highs, lows=lows, volumes=volumes, source=source)


def _clean_numbers(values: list[Any]) -> list[float]:
    result = []
    for value in values:
        number = _to_float(value)
        if number is not None and math.isfinite(number):
            result.append(number)
    return result


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _availability_status(count: int, total: int) -> str:
    if count <= 0:
        return "unavailable"
    if count >= total:
        return "available"
    return "partial"


def _average(values: list[float]) -> Optional[float]:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
