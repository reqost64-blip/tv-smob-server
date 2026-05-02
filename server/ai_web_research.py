from __future__ import annotations

import json
import re
import socket
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config


ASSETS = "XAUUSD, NAS100, DJ30, US500, BTCUSD"
BERLIN_TZ = ZoneInfo("Europe/Berlin")
WEB_SEARCH_TIMEOUT_MESSAGE = "AI web search долго отвечает. Повтори через минуту или спроси через /ask."
NEWS_FALLBACK_MESSAGE = (
    "Web search временно недоступен. Проверь вручную важные события: "
    "CPI, PPI, NFP, FOMC, Fed speakers, unemployment claims, PMI."
)


def answer_with_web_search(question: str, user_context: str = "") -> str:
    needs_web = _needs_fresh_data(question)
    prompt = _base_prompt(user_context) + "\n\nВопрос пользователя:\n" + question
    return _call_responses_api(prompt, use_web=needs_web)


def get_market_news_today() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Задача: рыночные новости на сегодня, {today}, timezone Europe/Berlin.
Активы только: {ASSETS}.

Верни строго в таком виде:
⚡ ВЫВОД
1-2 короткие строки.

▌ СОБЫТИЯ
1. HH:MM Berlin — событие
   Риск: высокий/средний/низкий

▌ РИСК ПО АКТИВАМ
XAUUSD: высокий/средний/низкий
NAS100: высокий/средний/низкий
DJ30: высокий/средний/низкий
US500: высокий/средний/низкий
BTCUSD: высокий/средний/низкий

▌ AI-КОММЕНТАРИЙ
Коротко: где вероятна волатильность и что контролировать.

Источники:
1. Короткое название источника

Правила: русский язык, максимум 8 пунктов событий, без длинных URL, не выдумывай события.
"""
    response = _call_responses_api(prompt, use_web=True)
    if _is_web_search_failure(response):
        return response + "\n\n" + NEWS_FALLBACK_MESSAGE
    return response


def get_economic_calendar_today() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Задача: экономический календарь на сегодня, {today}, timezone Europe/Berlin.
Активы только: {ASSETS}.
Для каждого события: время Europe/Berlin, валюта, событие, важность, затронутые активы.
Формат как для рыночной сводки: вывод, события, риск по активам, AI-комментарий, источники.
Русский язык, максимум 8 событий, без длинных URL. Не выдумывай события.
Если подтверждения нет, напиши: "не нашёл подтверждения".
"""
    return _call_responses_api(prompt, use_web=True)


def get_asset_impact_summary(asset: str) -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Задача: объяснить, что влияет на {asset} сегодня, {today}, timezone Europe/Berlin.
Фокус: подтверждённые драйверы рынка, календарь, доходности/доллар/риск-аппетит, если релевантно.
Формат: вывод, события, риск, AI-комментарий, источники.
Русский язык, максимум 8 пунктов, без длинных URL, без торговых приказов.
"""
    return _call_responses_api(prompt, use_web=True)


def get_market_today_summary() -> str:
    today = _today_berlin()
    prompt = _base_prompt() + f"""
Задача: краткий риск-обзор рынка на сегодня, {today}, timezone Europe/Berlin.
Активы только: {ASSETS}.
Включи high impact events, зоны возможной волатильности, активы с повышенным новостным риском.
Любое риск-действие формулируй только как требующее pending approval и /confirm.
Формат как для рыночной сводки: вывод, события, риск по активам, AI-комментарий, источники.
Русский язык, максимум 8 пунктов, без длинных URL.
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
                "search_context_size": "low",
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
        with urllib.request.urlopen(request, timeout=config.OPENAI_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        return WEB_SEARCH_TIMEOUT_MESSAGE if tools else f"AI response timed out: {exc}"
    except Exception as exc:
        if "timed out" in str(exc).lower():
            return WEB_SEARCH_TIMEOUT_MESSAGE if tools else f"AI response timed out: {exc}"
        return f"AI web research error: {exc}"

    text = _extract_output_text(data)
    if not text:
        return "Не нашёл подтверждённых данных."
    sources = _extract_sources(data)
    text = _remove_inline_urls(text)
    if sources and "источники" not in text.lower() and "sources" not in text.lower():
        text = text.rstrip() + "\n\nИсточники:\n" + "\n".join(f"{index}. {source}" for index, source in enumerate(sources[:5], start=1))
    return _trim_telegram_answer(text)


def _base_prompt(user_context: str = "") -> str:
    return f"""
Ты информационный рыночный AI-ассистент для Telegram-бота трейдинг-системы.
Правила безопасности:
- Не открывай и не закрывай сделки.
- Не меняй риск-настройки.
- Не утверждай, что действие уже применено.
- Если предлагаешь риск-действие, скажи, что оно требует pending approval и /confirm.
- Не выдумывай события и источники.
- Пиши кратко: максимум 8 пунктов, без воды.
- Язык ответа: русский.
- Не вставляй длинные URL в текст.
Активы: {ASSETS}.
Контекст: {user_context or "admin Telegram chat for a demo-first TradingView to MT5 bridge"}.
"""


def _needs_fresh_data(question: str) -> bool:
    normalized = question.lower()
    fresh_markers = (
        "сегодня",
        "сейчас",
        "новост",
        "календар",
        "падает",
        "растёт",
        "растет",
        "движ",
        "рынок",
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
                sources.append(_short_source(title, url))
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for annotation in content.get("annotations", []):
                if annotation.get("type") == "url_citation":
                    title = annotation.get("title") or annotation.get("url")
                    url = annotation.get("url")
                    if title and url:
                        sources.append(_short_source(title, url))
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
            if bullet_count > 8:
                continue
        kept.append(line)
    result = "\n".join(kept)
    return result[:3500]


def _remove_inline_urls(text: str) -> str:
    text = re.sub(r"\s*\[[^\]]+\]\(https?://[^)]+\)", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _short_source(title: str, url: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    if title and not title.startswith("http"):
        return title[:80]
    host = re.sub(r"^https?://", "", url or "").split("/")[0]
    return host[:80] if host else "source"


def _today_berlin() -> str:
    return datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")


def _is_web_search_failure(response: str) -> bool:
    return response.startswith(WEB_SEARCH_TIMEOUT_MESSAGE) or response.startswith("AI web research error:")
