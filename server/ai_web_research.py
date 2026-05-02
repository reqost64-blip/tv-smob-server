from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config


ASSETS = "XAUUSD, NAS100, DJ30, US500, BTCUSD"
BERLIN_TZ = ZoneInfo("Europe/Berlin")


def answer_with_web_search(question: str, user_context: str = "") -> str:
    needs_web = _needs_fresh_data(question)
    prompt = _base_prompt(user_context) + "\n\nUser question:\n" + question
    return _call_responses_api(prompt, use_web=needs_web)


def get_market_news_today() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Task: Give the main market news for today, {today}, for a Telegram trading bot.
Cover USD, US indices, gold, crypto, and oil only if relevant.
Output in Russian. Max 10 bullets. Start with a conclusion, then events, then asset risk.
Mention source names/links when available. If not confirmed, write "не нашёл подтверждения".
"""
    return _call_responses_api(prompt, use_web=True)


def get_economic_calendar_today() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Task: Find important economic calendar events for today, {today}.
Use Europe/Berlin time. For each event include:
time, currency, event, importance, affected assets from {ASSETS}.
Output in Russian. Max 10 bullets. Do not invent events. If data is unavailable, say "не нашёл подтверждения".
"""
    return _call_responses_api(prompt, use_web=True)


def get_asset_impact_summary(asset: str) -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Task: Explain what is affecting {asset} today, {today}.
Focus on confirmed market drivers, scheduled events, and risk.
Output in Russian. Max 10 bullets. Start with the conclusion.
"""
    return _call_responses_api(prompt, use_web=True)


def get_market_today_summary() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Task: Give a short trading risk overview for today, {today}.
Include high impact events, volatility risk, assets to avoid before news, and any action suggestions only as confirmation-required ideas.
Assets: {ASSETS}.
Output in Russian. Max 10 bullets. Start with conclusion, then events, then asset risk.
"""
    return _call_responses_api(prompt, use_web=True)


def _call_responses_api(prompt: str, use_web: bool) -> str:
    if not config.OPENAI_API_KEY:
        return "AI web research недоступен: OPENAI_API_KEY не задан."

    tools = []
    if use_web and config.ENABLE_AI_WEB_SEARCH:
        tools.append(
            {
                "type": "web_search",
                "user_location": {
                    "type": "approximate",
                    "country": "DE",
                    "timezone": "Europe/Berlin",
                },
            }
        )

    payload = {
        "model": config.OPENAI_MODEL,
        "input": prompt,
        "tools": tools,
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"] if tools else [],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return f"AI web research error: {exc}"

    text = _extract_output_text(data)
    if not text:
        return "Не нашёл подтверждения."
    sources = _extract_sources(data)
    if sources:
        text = text.rstrip() + "\n\nSources: " + "; ".join(sources[:5])
    return _trim_telegram_answer(text)


def _base_prompt(user_context: str = "") -> str:
    return f"""
You are an informational market research assistant for a Telegram bot.
Safety rules:
- Do not open or close trades.
- Do not change risk settings.
- Do not claim that any action was applied.
- If suggesting a risk action, say it requires /confirm after a pending approval.
- Do not invent events or sources.
- Keep the answer short: maximum 10 bullets, no filler.
- Answer in Russian unless the user explicitly asks otherwise.
Assets: {ASSETS}.
User context: {user_context or "admin Telegram chat for a demo-first TradingView to MT5 bridge"}.
"""


def _needs_fresh_data(question: str) -> bool:
    normalized = question.lower()
    fresh_markers = (
        "сегодня",
        "сейчас",
        "новост",
        "календар",
        "падает",
        "растет",
        "движ",
        "рынк",
        "gold",
        "xau",
        "nas100",
        "us500",
        "sp500",
        "dj30",
        "btc",
        "crypto",
        "usd",
        "fed",
        "cpi",
        "nfp",
        "fomc",
        "oil",
    )
    return any(marker in normalized for marker in fresh_markers)


def _extract_output_text(data: dict) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text") and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _extract_sources(data: dict) -> list[str]:
    sources: list[str] = []
    for item in data.get("output", []):
        for source in ((item.get("action") or {}).get("sources") or []):
            title = source.get("title") or source.get("url")
            url = source.get("url")
            if title and url:
                sources.append(f"{title}: {url}")
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for annotation in content.get("annotations", []):
                if annotation.get("type") == "url_citation":
                    title = annotation.get("title") or annotation.get("url")
                    url = annotation.get("url")
                    if title and url:
                        sources.append(f"{title}: {url}")
    deduped = []
    for source in sources:
        if source not in deduped:
            deduped.append(source)
    return deduped


def _trim_telegram_answer(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    bullet_count = 0
    kept: list[str] = []
    for line in lines:
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line):
            bullet_count += 1
            if bullet_count > 10:
                continue
        kept.append(line)
    result = "\n".join(kept)
    return result[:3500]


def _today_berlin() -> str:
    return datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
