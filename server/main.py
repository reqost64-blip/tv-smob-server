import json
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .database import init_db
from .models import (
    AckRequest,
    ErrorResponse,
    ExecutionReport,
    OkResponse,
    SettingsChangeRequest,
    WebhookPayload,
)
from .settings_store import audit_log, get_setting, list_settings, parse_value, record_audit_event, set_setting
from .validators import validate_signal
from . import queue as q
from .symbol_mapper import load_symbols
from .telegram_bot import (
    NOTIFY_EXECUTION_STATUSES,
    handle_command,
    notify_close_signal,
    notify_event,
    parse_telegram_update,
    send_telegram_message,
    validate_change,
)

app = FastAPI(title="TradingView → MT5 Bridge", version="1.0.0")


SYMBOL_ALIASES = {
    "SP500": "US500",
    "US500": "US500",
    "NAS100": "NAS100",
    "DJ30": "DJ30",
    "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD",
}


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    load_symbols()


# ── 1. Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "status": "running"}


# ── 2. Webhook ─────────────────────────────────────────────────────────────────

@app.post("/api/webhook/tradingview")
async def webhook_tradingview(request: Request):
    try:
        body = await request.json()
    except Exception:
        notify_event("rejected_signal", details="Invalid JSON body")
        return err("Invalid JSON body")

    try:
        payload = WebhookPayload(**body)
    except Exception as exc:
        notify_event("rejected_signal", details=f"Payload validation error: {exc}")
        return err(f"Payload validation error: {exc}")

    if payload.secret != config.WEBHOOK_SECRET:
        notify_event("rejected_signal", payload.signal_id, "Invalid secret")
        return err("Invalid secret", status=403)

    validation_error = validate_signal(payload)
    if validation_error:
        notify_event("rejected_signal", payload.signal_id, validation_error)
        return err(validation_error)

    if payload.action == "open" and not get_setting("trading_enabled", config.TRADING_ENABLED):
        notify_event("rejected_signal", payload.signal_id, "Trading disabled by server setting")
        return err("Trading disabled by server setting")

    paused_symbol = normalize_control_symbol(payload.mt5_symbol or payload.symbol)
    if payload.action == "open" and paused_symbol and is_symbol_paused(paused_symbol):
        notify_event("rejected_signal", payload.signal_id, f"{paused_symbol} paused by server setting")
        return err(f"{paused_symbol} paused by server setting")

    if q.signal_exists(payload.signal_id):
        notify_event("rejected_signal", payload.signal_id, "Duplicate signal_id")
        return err(f"Duplicate signal_id: {payload.signal_id}")

    notify_event(
        "webhook_signal_received",
        payload.signal_id,
        f"{payload.symbol} {payload.side} {payload.action}",
    )
    q.enqueue(payload)
    if payload.action == "close":
        notify_close_signal(payload)
    notify_event("command_queued", payload.signal_id)
    return {"ok": True, "signal_id": payload.signal_id, "status": "queued"}


# ── 3. MT5 fetch next command ──────────────────────────────────────────────────

@app.get("/api/mt5/commands")
async def mt5_get_command():
    command = q.fetch_next_queued()
    if command is None:
        return {"ok": True, "command": None}

    payload_data = json.loads(command["payload"])
    notify_event("mt5_command_sent", command["signal_id"])
    return {
        "ok": True,
        "command": {
            "signal_id": command["signal_id"],
            "status": "sent",
            "payload": payload_data,
        },
    }


# ── 4. MT5 acknowledge ────────────────────────────────────────────────────────

@app.post("/api/mt5/ack")
async def mt5_ack(body: AckRequest):
    updated = q.acknowledge(body.signal_id)
    if not updated:
        return err(f"signal_id '{body.signal_id}' not found in status=sent")
    notify_event("ack_received", body.signal_id)
    return {"ok": True, "signal_id": body.signal_id, "status": "acknowledged"}


# ── 5. MT5 execution report ───────────────────────────────────────────────────

@app.post("/api/mt5/execution-report")
async def mt5_execution_report(report: ExecutionReport):
    q.save_execution_report(report)
    notify_event(
        "execution_report_received",
        report.signal_id,
        f"status: {report.status}",
    )
    normalized_status = report.status.lower()
    if normalized_status in NOTIFY_EXECUTION_STATUSES:
        notify_event(normalized_status, report.signal_id, report.message)
    return {"ok": True, "signal_id": report.signal_id}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return err("Invalid JSON body")

    chat_id, text = parse_telegram_update(update)
    if not chat_id or not text:
        return {"ok": True, "handled": False}

    if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
        return err("Unauthorized chat", status=403)

    response = handle_command(text, chat_id)
    send_telegram_message(response)
    return {"ok": True, "handled": True}


@app.get("/api/settings")
async def api_get_settings():
    return {"ok": True, "settings": list_settings()}


@app.post("/api/settings")
async def api_post_settings(body: SettingsChangeRequest, request: Request):
    header_secret = request.headers.get("x-webhook-secret", "")
    if body.secret != config.WEBHOOK_SECRET and header_secret != config.WEBHOOK_SECRET:
        return err("Invalid secret", status=403)
    new_value = parse_value(str(body.value))
    validation_error = validate_change(body.key, new_value, None)
    if validation_error:
        return err(validation_error)
    old_value = get_setting(body.key)
    set_setting(body.key, new_value)
    record_audit_event("api_setting_updated", "api", body.key, old_value, new_value)
    return {"ok": True, "key": body.key, "value": new_value}


@app.get("/api/audit-log")
async def api_audit_log(limit: int = 100):
    return {"ok": True, "audit_log": audit_log(limit)}


def normalize_control_symbol(symbol: str | None) -> str | None:
    if not symbol:
        return None
    return SYMBOL_ALIASES.get(symbol.upper())


def is_symbol_paused(symbol: str) -> bool:
    paused_until = get_setting(f"symbol_paused_until_{symbol}", "")
    if not paused_until:
        return False
    try:
        expiry = datetime.fromisoformat(str(paused_until))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expiry
