from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import bias_store, signal_store


VALID_VERDICT = "VALID_SIGNAL"
WATCH_VERDICTS = {"WATCH_ONLY", "WAIT_CONFIRMATION"}


def score_signal(signal: dict, now: Optional[datetime] = None, duplicate: bool = False) -> dict:
    current = now or datetime.now(timezone.utc)
    signal = dict(signal)
    reasons = list(signal.get("reasons") or [])
    rejection = list(signal.get("rejection_reasons") or [])

    if duplicate:
        signal.update({"status": "rejected", "verdict": "DUPLICATE", "score": 0, "confidence": 0, "risk_level": "HIGH"})
        signal["rejection_reasons"] = rejection + ["duplicate signal"]
        return signal

    if signal.get("parse_status") == "unparsed":
        return reject(signal, "unparsed signal")
    if signal.get("parse_status") == "partial":
        rejection.extend(signal.get("parse_errors") or ["partial parse"])

    required = {
        "symbol": signal.get("symbol"),
        "direction": signal.get("direction"),
        "entry": signal.get("entry") if signal.get("entry") is not None else signal.get("entry_zone_low"),
        "sl": signal.get("sl"),
    }
    for key, value in required.items():
        if value is None or value == "":
            rejection.append(f"missing {key}")

    age = signal_age_minutes(signal, current)
    if age is not None and age > 10:
        signal.update({"status": "expired", "verdict": "EXPIRED", "score": 0, "confidence": 0, "risk_level": "HIGH"})
        signal["rejection_reasons"] = rejection + ["signal older than 10 minutes for scalp"]
        return signal

    rr = risk_reward(signal)
    if rr is None:
        rejection.append("risk/reward unavailable")
    elif rr < 1.0:
        rejection.append("RR below 1.0")
    if stop_too_large(signal):
        rejection.append("SL too large")

    if rejection:
        signal.update({"status": "rejected", "verdict": "REJECTED", "score": 0, "confidence": 0, "risk_level": "HIGH"})
        signal["rejection_reasons"] = rejection
        return signal

    live_bias = latest_live_bias_for(signal.get("symbol"))
    source_stats = source_stats_for(signal.get("source_name"))
    market_component, market_reasons = market_confirmation_component(signal)
    bias_component, bias_reasons, bias_conflict = bias_component_score(signal, live_bias)
    source_component, source_reason = source_component_score(source_stats)
    rr_component = min(15.0, (rr or 0) / 2.0 * 15.0)
    macro_component, macro_reason, macro_high = macro_component_score(live_bias)
    freshness_component = freshness_component_score(age)

    score = market_component + bias_component + source_component + rr_component + macro_component + freshness_component
    risk_level = risk_level_for(signal, live_bias, macro_high, bias_conflict)
    if macro_high and score >= 70:
        score = min(score, 69)
        reasons.append("high macro risk caps aggressive signal")

    reasons.extend(market_reasons + bias_reasons)
    if source_reason:
        reasons.append(source_reason)
    if macro_reason:
        reasons.append(macro_reason)
    if rr is not None:
        reasons.append(f"RR {rr:.2f}")
    if age is not None:
        reasons.append(f"fresh {round(age, 1)}m")

    if score >= 70:
        verdict = VALID_VERDICT
        status = "validated"
    elif score >= 55:
        verdict = "WAIT_CONFIRMATION" if bias_conflict or market_component < 22 else "WATCH_ONLY"
        status = "validated"
    else:
        verdict = "REJECTED"
        status = "rejected"
        rejection.append("score below 55")

    signal.update(
        {
            "status": status,
            "verdict": verdict,
            "score": round(max(0, min(100, score)), 1),
            "confidence": round(max(0, min(100, score)), 1),
            "risk_level": risk_level,
            "reasons": reasons[:12],
            "rejection_reasons": rejection,
            "rr": round(rr, 2) if rr is not None else None,
            "live_bias_direction": live_bias.get("direction") if live_bias else None,
            "live_bias_confidence": live_bias.get("confidence") if live_bias else None,
            "source_trust_score": source_stats.get("trust_score") if source_stats else None,
        }
    )
    return signal


def reject(signal: dict, reason: str) -> dict:
    signal = dict(signal)
    signal.update({"status": "rejected", "verdict": "REJECTED", "score": 0, "confidence": 0, "risk_level": "HIGH"})
    signal["rejection_reasons"] = list(signal.get("rejection_reasons") or []) + [reason]
    return signal


def latest_live_bias_for(symbol: str | None) -> Optional[dict]:
    if not symbol:
        return None
    rows = bias_store.latest_live_bias(symbol=symbol)
    return rows[0] if rows else None


def source_stats_for(source_name: str | None) -> dict:
    if not source_name:
        return {}
    for row in signal_store.source_reliability():
        if row.get("source_name") == source_name:
            return row
    return {}


def market_confirmation_component(signal: dict) -> tuple[float, list[str]]:
    confirmations = signal.get("confirmations") or []
    if confirmations:
        score = min(35.0, 12.0 + 6.0 * len(confirmations))
        return score, [str(item) for item in confirmations[:4]]
    setup = str(signal.get("setup") or "").lower()
    if any(term in setup for term in ("vwap", "momentum", "ema", "breakout", "sweep")):
        return 23.0, [f"setup {signal.get('setup')}"]
    return 18.0, ["market confirmation pending"]


def bias_component_score(signal: dict, live_bias: Optional[dict]) -> tuple[float, list[str], bool]:
    if not live_bias:
        return 10.0, ["live bias unavailable"], False
    same = live_bias.get("direction") == signal.get("direction")
    confidence = float(live_bias.get("confidence") or 0)
    if same:
        return min(20.0, 11.0 + confidence / 10.0), [f"Live Bias {live_bias.get('direction')} {int(confidence)}%"], False
    return max(0.0, 8.0 - confidence / 12.0), [f"Live Bias conflict {live_bias.get('direction')} {int(confidence)}%"], True


def source_component_score(stats: dict) -> tuple[float, str]:
    trust = stats.get("trust_score") if stats else None
    if trust is None:
        return 7.5, "source trust pending"
    return max(0.0, min(15.0, float(trust) / 100.0 * 15.0)), f"source trust {trust}/100"


def macro_component_score(live_bias: Optional[dict]) -> tuple[float, str, bool]:
    risk = str((live_bias or {}).get("risk") or "UNKNOWN").upper()
    if risk == "HIGH":
        return 2.0, "macro/news/volatility risk HIGH", True
    if risk == "MEDIUM":
        return 6.0, "macro/news risk medium", False
    if risk == "LOW":
        return 10.0, "macro/news risk low", False
    return 5.0, "macro/news risk unknown", False


def freshness_component_score(age: Optional[float]) -> float:
    if age is None:
        return 3.0
    if age <= 2:
        return 5.0
    if age <= 5:
        return 4.0
    if age <= 10:
        return 2.5
    return 0.0


def risk_level_for(signal: dict, live_bias: Optional[dict], macro_high: bool, bias_conflict: bool) -> str:
    if macro_high or bias_conflict:
        return "HIGH"
    rr = risk_reward(signal)
    if rr is not None and rr >= 1.5 and str((live_bias or {}).get("risk") or "").upper() == "LOW":
        return "LOW"
    return "MEDIUM"


def risk_reward(signal: dict) -> Optional[float]:
    entry = signal_entry(signal)
    sl = to_float(signal.get("sl"))
    tp1 = to_float(signal.get("tp1"))
    direction = signal.get("direction")
    if entry is None or sl is None or tp1 is None or entry == sl:
        return None
    risk = abs(entry - sl)
    reward = tp1 - entry if direction == "LONG" else entry - tp1
    if reward <= 0 or risk <= 0:
        return 0.0
    return reward / risk


def stop_too_large(signal: dict) -> bool:
    entry = signal_entry(signal)
    sl = to_float(signal.get("sl"))
    if entry is None or sl is None or entry <= 0:
        return False
    return abs(entry - sl) / entry > 0.05


def signal_entry(signal: dict) -> Optional[float]:
    if signal.get("entry") is not None:
        return to_float(signal.get("entry"))
    low = to_float(signal.get("entry_zone_low"))
    high = to_float(signal.get("entry_zone_high"))
    if low is not None and high is not None:
        return (low + high) / 2
    return None


def signal_age_minutes(signal: dict, now: datetime) -> Optional[float]:
    try:
        parsed = datetime.fromisoformat(str(signal.get("timestamp")).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60)


def to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
