from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


SUPPORTED_SYMBOLS = {"NAS100", "SP500", "DJ30", "XAUUSD", "BTCUSD", "GER40"}
SYMBOL_ALIASES = {
    "US500": "SP500",
    "SPX500": "SP500",
    "S&P500": "SP500",
    "S&P": "SP500",
    "DAX": "GER40",
    "GER40FT": "GER40",
    "GOLD": "XAUUSD",
    "XAU": "XAUUSD",
    "BTC": "BTCUSD",
}
DIRECTION_ALIASES = {
    "BUY": "LONG",
    "LONG": "LONG",
    "BULLISH": "LONG",
    "SELL": "SHORT",
    "SHORT": "SHORT",
    "BEARISH": "SHORT",
}


def parse_tradingview_signal(payload: dict[str, Any]) -> dict:
    signal = base_signal(source_type="tradingview", source_name=str(payload.get("source") or "TradingView"))
    signal.update(
        {
            "symbol": normalize_symbol(payload.get("symbol")),
            "direction": normalize_direction(payload.get("direction") or payload.get("side")),
            "setup": clean_text(payload.get("setup") or payload.get("strategy") or payload.get("alert_name")),
            "entry": to_float(payload.get("entry")),
            "entry_zone_low": to_float(payload.get("entry_zone_low")),
            "entry_zone_high": to_float(payload.get("entry_zone_high")),
            "sl": to_float(payload.get("sl") or payload.get("stop_loss")),
            "tp1": to_float(payload.get("tp1")),
            "tp2": to_float(payload.get("tp2")),
            "tp3": to_float(payload.get("tp3")),
            "timeframe": clean_text(payload.get("timeframe") or payload.get("tf")),
            "confidence": to_float(payload.get("confidence")) or 0,
            "raw_text": clean_text(payload.get("message") or payload.get("raw_text")),
            "timestamp": normalize_time(payload.get("timestamp") or payload.get("time")) or signal["timestamp"],
        }
    )
    return finalize_parse(signal)


def parse_manual_signal(source_name: str | None, text: str) -> dict:
    signal = base_signal(source_type="manual", source_name=clean_text(source_name) or "Manual")
    raw = text or ""
    upper = raw.upper()
    signal.update(
        {
            "raw_text": sanitize_raw_text(raw),
            "symbol": parse_symbol(upper),
            "direction": parse_direction(upper),
            "entry": first_number_after(raw, r"(?:ENTRY|ENTRADA|ВХОД|BUY|SELL|LONG|SHORT|@)"),
            "sl": first_number_after(raw, r"(?:SL|STOP|STOP LOSS|СТОП)"),
            "tp1": first_number_after(raw, r"(?:TP1|TAKE PROFIT 1|TARGET 1)"),
            "tp2": first_number_after(raw, r"(?:TP2|TAKE PROFIT 2|TARGET 2)"),
            "tp3": first_number_after(raw, r"(?:TP3|TAKE PROFIT 3|TARGET 3)"),
            "timeframe": parse_timeframe(upper),
            "setup": parse_setup(raw),
        }
    )
    zone = parse_entry_zone(raw)
    if zone:
        signal["entry_zone_low"], signal["entry_zone_high"] = zone
        if signal["entry"] is None:
            signal["entry"] = round((zone[0] + zone[1]) / 2, 5)
    return finalize_parse(signal)


def base_signal(source_type: str, source_name: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "signal_id": f"sig_{uuid4().hex[:16]}",
        "timestamp": now,
        "source_type": source_type,
        "source_name": source_name,
        "symbol": None,
        "direction": None,
        "setup": None,
        "entry": None,
        "entry_zone_low": None,
        "entry_zone_high": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "timeframe": None,
        "raw_text": "",
        "parse_status": "unparsed",
        "expiry_minutes": 20,
        "status": "new",
        "verdict": "WAIT_CONFIRMATION",
        "score": 0,
        "confidence": 0,
        "risk_level": "HIGH",
        "reasons": [],
        "rejection_reasons": [],
    }


def finalize_parse(signal: dict) -> dict:
    missing = []
    for field in ("symbol", "direction"):
        if not signal.get(field):
            missing.append(field)
    has_entry = signal.get("entry") is not None or (signal.get("entry_zone_low") is not None and signal.get("entry_zone_high") is not None)
    if not has_entry:
        missing.append("entry")
    if signal.get("sl") is None:
        missing.append("sl")
    signal["parse_errors"] = [f"missing_{field}" for field in missing]
    if not signal.get("symbol") and not signal.get("direction") and not has_entry:
        signal["parse_status"] = "unparsed"
    elif missing:
        signal["parse_status"] = "partial"
    else:
        signal["parse_status"] = "parsed"
    if signal.get("entry") is None and signal.get("entry_zone_low") is not None and signal.get("entry_zone_high") is not None:
        signal["entry"] = round((float(signal["entry_zone_low"]) + float(signal["entry_zone_high"])) / 2, 5)
    if signal.get("entry_zone_low") is None and signal.get("entry") is not None:
        signal["entry_zone_low"] = signal["entry"]
    if signal.get("entry_zone_high") is None and signal.get("entry") is not None:
        signal["entry_zone_high"] = signal["entry"]
    signal["dedupe_key"] = build_dedupe_key(signal)
    return signal


def normalize_symbol(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"[^A-Z0-9&]", "", str(value).upper())
    text = SYMBOL_ALIASES.get(text, text)
    return text if text in SUPPORTED_SYMBOLS else None


def normalize_direction(value: Any) -> Optional[str]:
    if value is None:
        return None
    return DIRECTION_ALIASES.get(str(value).strip().upper())


def parse_symbol(text: str) -> Optional[str]:
    for token in sorted(SUPPORTED_SYMBOLS | set(SYMBOL_ALIASES), key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", text):
            return normalize_symbol(token)
    return None


def parse_direction(text: str) -> Optional[str]:
    for token in ("LONG", "SHORT", "BUY", "SELL", "BULLISH", "BEARISH"):
        if re.search(rf"\b{token}\b", text):
            return normalize_direction(token)
    return None


def parse_entry_zone(text: str) -> Optional[tuple[float, float]]:
    match = re.search(r"(?:ENTRY|ZONE|ВХОД)[^\d-]*([0-9]+(?:[.,][0-9]+)?)\s*(?:-|–|—|TO)\s*([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if not match:
        return None
    first = to_float(match.group(1))
    second = to_float(match.group(2))
    if first is None or second is None:
        return None
    return (min(first, second), max(first, second))


def first_number_after(text: str, label_pattern: str) -> Optional[float]:
    match = re.search(label_pattern + r"[^\d-]*([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if not match:
        return None
    return to_float(match.group(1))


def parse_timeframe(text: str) -> Optional[str]:
    match = re.search(r"\b([MHD][0-9]{1,3}|[0-9]{1,3}M|[0-9]{1,2}H)\b", text)
    return match.group(1).lower() if match else None


def parse_setup(text: str) -> Optional[str]:
    match = re.search(r"(?:SETUP|СЕТАП)\s*[:\-]\s*([^\n\r]+)", text, re.I)
    if match:
        return clean_text(match.group(1))[:80]
    lowered = text.lower()
    for setup in ("vwap reclaim", "vwap rejection", "liquidity sweep", "momentum scalp", "ema trend", "compression breakout", "breakout retest"):
        if setup in lowered:
            return setup.upper().replace(" ", "_")
    return None


def normalize_time(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def build_dedupe_key(signal: dict) -> str:
    bucket = str(signal.get("timestamp") or "")[:15]
    parts = [
        signal.get("source_name") or "",
        signal.get("symbol") or "",
        signal.get("direction") or "",
        signal.get("setup") or "",
        str(round(float(signal.get("entry") or 0), 2)),
        str(round(float(signal.get("sl") or 0), 2)),
        bucket,
    ]
    return "|".join(parts).upper()


def sanitize_raw_text(value: str) -> str:
    text = clean_text(value) or ""
    text = re.sub(r"(token|secret|password|api[_-]?key)\s*[:=]\s*\S+", r"\1=***", text, flags=re.I)
    return text[:2000]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
