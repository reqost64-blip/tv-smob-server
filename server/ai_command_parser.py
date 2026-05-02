from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from . import config
from .models import NaturalLanguageCommandResult


SYMBOLS = ("XAUUSD", "NAS100", "DJ30", "US500", "BTCUSD")
SYMBOL_ALIASES = {
    "xauusd": "XAUUSD",
    "gold": "XAUUSD",
    "золото": "XAUUSD",
    "nas100": "NAS100",
    "nasdaq": "NAS100",
    "нас100": "NAS100",
    "dj30": "DJ30",
    "dow": "DJ30",
    "dow30": "DJ30",
    "us500": "US500",
    "sp500": "US500",
    "s&p500": "US500",
    "btcusd": "BTCUSD",
    "btc": "BTCUSD",
    "биткоин": "BTCUSD",
}


def parse_natural_language_command(text: str) -> NaturalLanguageCommandResult:
    if config.OPENAI_API_KEY:
        parsed = _parse_with_openai(text)
        if parsed:
            return parsed
    return _parse_with_regex(text)


def _parse_with_openai(text: str) -> NaturalLanguageCommandResult | None:
    schema_hint = {
        "intent": "change_setting | pause_trading | resume_trading | show_settings | show_status | unknown",
        "symbol": "XAUUSD | NAS100 | DJ30 | US500 | BTCUSD | null",
        "setting_key": "string | null",
        "operation": "set | increase_percent | decrease_percent | enable | disable | null",
        "value": "number | boolean | null",
        "requires_confirmation": True,
        "confidence": 0.0,
    }
    prompt = (
        "Parse this Russian Telegram trading-control command into strict JSON only. "
        "Allowed symbols: XAUUSD, NAS100, DJ30, US500, BTCUSD. "
        "For symbol lot changes use setting_key symbol_lot_multiplier_<SYMBOL>. "
        "For trading pause/resume use setting_key trading_enabled. "
        "For dry run use setting_key dry_run. "
        "Percent values must be decimals, so 20 percent is 0.2. "
        "Never invent unknown symbols. Schema: "
        f"{json.dumps(schema_hint)}\nCommand: {text}"
    )
    payload = {
        "model": config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": "Return valid JSON only. No prose."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return NaturalLanguageCommandResult(**json.loads(content))
    except Exception:
        return None


def _parse_with_regex(text: str) -> NaturalLanguageCommandResult:
    normalized = " ".join(text.lower().strip().split())
    symbol = _extract_symbol(normalized)

    if re.search(r"\b(покажи|какие|какой|настройки)\b", normalized) and "настрой" in normalized:
        return _result("show_settings", confidence=0.9, requires_confirmation=False)
    if "риск" in normalized:
        return _result("show_status", confidence=0.85, requires_confirmation=False)
    if "последн" in normalized and ("сдел" in normalized or "трейд" in normalized):
        return _result("show_last_trade", confidence=0.85, requires_confirmation=False)

    if re.search(r"\b(останови|пауза|выключи)\b", normalized) and "торгов" in normalized:
        return _result(
            "pause_trading",
            "trading_enabled",
            operation="disable",
            value=False,
            confidence=0.95,
        )
    if re.search(r"\b(включи|запусти|возобнови)\b", normalized) and "торгов" in normalized:
        return _result(
            "resume_trading",
            "trading_enabled",
            operation="enable",
            value=True,
            confidence=0.95,
        )
    if "dry run" in normalized or "dryrun" in normalized or "драй ран" in normalized:
        if re.search(r"\b(включи|on)\b", normalized):
            return _result("change_setting", "dry_run", operation="enable", value=True, confidence=0.95)
        if re.search(r"\b(выключи|off)\b", normalized):
            return _result("change_setting", "dry_run", operation="disable", value=False, confidence=0.95)

    if "лот" in normalized and symbol:
        setting_key = f"symbol_lot_multiplier_{symbol}"
        set_match = re.search(r"(?:поставь|установи|сделай)\s+лот\s+([0-9]+(?:[.,][0-9]+)?)", normalized)
        if set_match:
            return _result(
                "change_setting",
                setting_key,
                symbol=symbol,
                operation="set",
                value=_number(set_match.group(1)),
                confidence=0.9,
            )
        pct_match = re.search(r"(повысь|увеличь|подними|уменьши|снизь)\s+лот\s+на\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:%|процент)", normalized)
        if pct_match:
            operation = "increase_percent" if pct_match.group(1) in ("повысь", "увеличь", "подними") else "decrease_percent"
            return _result(
                "change_setting",
                setting_key,
                symbol=symbol,
                operation=operation,
                value=_number(pct_match.group(2)) / 100,
                confidence=0.9,
            )

    return _result("unknown", confidence=0.0)


def _extract_symbol(text: str) -> str | None:
    for alias, symbol in SYMBOL_ALIASES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
            return symbol
    return None


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _result(
    intent: str,
    setting_key: str | None = None,
    *,
    symbol: str | None = None,
    operation: str | None = None,
    value: Any = None,
    confidence: float,
    requires_confirmation: bool = True,
) -> NaturalLanguageCommandResult:
    return NaturalLanguageCommandResult(
        intent=intent,
        symbol=symbol,
        setting_key=setting_key,
        operation=operation,
        value=value,
        requires_confirmation=requires_confirmation,
        confidence=confidence,
    )
