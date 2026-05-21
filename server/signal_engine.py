from __future__ import annotations

from . import signal_store
from .signal_parser import parse_manual_signal, parse_tradingview_signal
from .signal_scoring import score_signal
from .signal_sources import scan_market


def process_tradingview_signal(payload: dict, dry_run: bool = False) -> dict:
    parsed = parse_tradingview_signal(payload or {})
    return process_normalized_signal(parsed, dry_run=dry_run)


def process_manual_signal(payload: dict, dry_run: bool = True) -> dict:
    parsed = parse_manual_signal(payload.get("source_name"), payload.get("text") or "")
    return process_normalized_signal(parsed, dry_run=dry_run)


def process_normalized_signal(signal: dict, dry_run: bool = False) -> dict:
    duplicate = signal_store.is_duplicate(signal.get("dedupe_key")) if not dry_run else False
    scored = score_signal(signal, duplicate=duplicate)
    should_send = should_send_signal(scored, duplicate=duplicate)
    if not dry_run and not duplicate:
        signal_store.save_signal(scored, telegram_sent=False, send_reason="not_sent")
    return {
        "ok": True,
        "dry_run": dry_run,
        "duplicate": duplicate,
        "should_send": should_send,
        "signal": public_signal(scored),
    }


def scan_signals(allow_network: bool = False, dry_run: bool = True, symbol: str | None = None) -> dict:
    scan = scan_market(allow_network=allow_network, symbol=symbol)
    results = []
    for candidate in scan.get("signals") or []:
        results.append(process_tradingview_signal(candidate, dry_run=dry_run))
    return {
        "ok": True,
        "dry_run": dry_run,
        "scanner_result": scan.get("scanner_result"),
        "source_availability": scan.get("source_availability"),
        "signals": [item.get("signal") for item in results],
        "processed": len(results),
    }


def should_send_signal(signal: dict, duplicate: bool = False) -> bool:
    return (
        not duplicate
        and signal.get("verdict") == "VALID_SIGNAL"
        and float(signal.get("score") or 0) >= 70
        and signal.get("status") not in {"expired", "rejected"}
        and signal.get("risk_level") != "HIGH"
    )


def public_signal(signal: dict) -> dict:
    result = dict(signal)
    result.pop("raw_text", None)
    return result
