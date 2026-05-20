import asyncio
import base64
import binascii
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config
from . import account_store as acct
from . import bias_store
from .bias_engine import calculate_bias_report
from .database import init_db
from .models import (
    AckRequest,
    AccountSnapshot,
    BotControlRequest,
    DailyReportTaskRequest,
    DealReport,
    ErrorResponse,
    ExecutionReport,
    NativeMT5AccountSnapshot,
    NativeMT5ControlRequest,
    NativeMT5Event,
    NativeMT5Heartbeat,
    NativeMT5Screenshot,
    OkResponse,
    PositionsSnapshot,
    SettingsChangeRequest,
    WebhookPayload,
)
from .settings_store import audit_log, get_setting, list_settings, parse_value, record_audit_event, set_setting
from .history_import import import_history_rows, parse_history_payload
from .strategy_optimizer import recommendations_payload, run_strategy_lab
from .strategy_test_lab import build_strategy_lab_report
from .validators import validate_signal
from . import queue as q
from .symbol_mapper import load_symbols
from .telegram_bot import (
    handle_command,
    handle_telegram_update,
    format_daily_report,
    format_native_mt5_event_message,
    format_native_screenshot_caption,
    notify_close_signal,
    notify_event,
    notify_execution,
    parse_telegram_update,
    send_telegram_photo,
    send_telegram_message,
    should_notify_execution,
    validate_change,
)
from .native_trade_notifications import (
    accounting_event_type,
    format_clean_trade_message,
    normalizeNativeTradeEvent,
)
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

app = FastAPI(title="Native MT5 Notification Server", version="1.1.0")

DASHBOARD_FILE = Path(__file__).parent.parent / "dashboard" / "index.html"


# ── Dashboard CORS (Access-Control-Allow-Origin: * for /api/dashboard/* only) ─

@app.middleware("http")
async def _dashboard_cors(request: Request, call_next):
    if request.method == "OPTIONS" and request.url.path.startswith("/api/dashboard/"):
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
    response = await call_next(request)
    if request.url.path.startswith("/api/dashboard/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
    return response


NATIVE_SCREENSHOT_EVENTS = {
    "opened",
    "trade_opened",
    "tp1_closed",
    "tp1_hit",
    "tp1_taken",
    "tp1_be",
    "tp2_closed",
    "tp2_hit",
    "tp2_taken",
    "tp2_silent",
    "tp3_closed",
    "tp3_hit",
    "tp3_taken",
    "be_moved",
    "position_closed",
    "trade_closed",
    "closed",
    "closed_by_signal",
}
MAX_NATIVE_SCREENSHOT_BYTES = 10 * 1024 * 1024
SCREENSHOT_DIR = config.runtime_data_path("screenshots")
SCREENSHOTS_TO_KEEP = 100
SCREENSHOTS_TO_KEEP_PER_BOT = 20
BERLIN_TZ = ZoneInfo("Europe/Berlin")
NY_TZ = ZoneInfo("America/New_York")
_daily_report_task: asyncio.Task | None = None
_bias_report_task: asyncio.Task | None = None
pending_messages: dict[str, dict] = {}


SYMBOL_ALIASES = {
    "SP500": "US500",
    "US500": "US500",
    "NAS100": "NAS100",
    "DJ30": "DJ30",
    "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD",
    "GER40": "GER40",
    "GER40FT": "GER40",
}


def pending_key(bot_id: str | None, symbol: str | None) -> str:
    return f"{bot_id or ''}_{symbol or ''}".upper()


def telegram_caption(text: str) -> str:
    text = str(text or "")
    if len(text) > 900:
        return text[:897].rstrip() + "..."
    return text


async def send_with_screenshot_timeout(bot_id: str | None, symbol: str | None, timeout: int = 8) -> None:
    key = pending_key(bot_id, symbol)
    await asyncio.sleep(timeout)
    pending = pending_messages.get(key)
    if not pending:
        return
    if pending.get("task") is not asyncio.current_task():
        return
    msg = pending_messages.pop(key, None)
    if msg:
        send_telegram_message(msg["text"])


def native_event_payload(event: NativeMT5Event) -> tuple[dict, bool]:
    event_type = str(event.event_type or "").strip().lower()
    payload = event.model_dump(mode="json", exclude={"secret"})
    if event_type == "be_moved":
        trade = acct.get_trade_for_notification(event)
        if trade and trade.get("tp1_done"):
            return payload, False
    else:
        trade = acct.get_trade_for_notification(event)
    if trade:
        if event_type in {"position_closed", "closed_by_signal"}:
            if trade.get("profit") is not None:
                payload["total_profit"] = trade["profit"]
            payload.setdefault("opened_at", trade.get("opened_at"))
            payload.setdefault("exit_price", trade.get("exit_price"))
            payload.setdefault("entry", trade.get("entry"))
            payload.setdefault("sl", trade.get("sl"))
            payload.setdefault("lot", trade.get("lot"))
            payload["tp1_done"] = bool(trade.get("tp1_done"))
            payload["tp2_done"] = bool(trade.get("tp2_done"))
            payload["tp3_done"] = bool(trade.get("tp3_done"))
            payload["be_done"] = bool(trade.get("be_done"))
            if trade.get("tp1_profit") is not None:
                payload["tp1_profit"] = trade["tp1_profit"]
            if trade.get("tp2_profit") is not None:
                payload["tp2_profit"] = trade["tp2_profit"]
        elif event_type in {"tp1_closed", "tp2_closed", "tp3_closed"}:
            payload["accumulated_profit"] = trade.get("profit")
            payload.setdefault("entry", trade.get("entry"))
            payload.setdefault("sl", trade.get("sl"))
            payload.setdefault("lot", trade.get("lot"))
            if trade.get("tp1_profit") is not None:
                payload["tp1_profit"] = trade["tp1_profit"]
            if trade.get("tp2_profit") is not None:
                payload["tp2_profit"] = trade["tp2_profit"]
    return payload, True


def direct_screenshot_from_payload(event: NativeMT5Event, payload: dict) -> Path | str | None:
    screenshot = payload.get("screenshot")
    if not screenshot:
        return None
    if isinstance(screenshot, dict):
        file_path = screenshot.get("file_path") or screenshot.get("path")
        image_base64 = screenshot.get("image_base64") or screenshot.get("base64") or screenshot.get("data")
    else:
        text = str(screenshot)
        file_path = text if Path(text).exists() else None
        image_base64 = None if file_path else text
    if file_path:
        return str(file_path)
    if not image_base64:
        return None
    image_bytes = decode_native_screenshot(str(image_base64))
    pseudo = NativeMT5Screenshot(
        secret=event.secret,
        source=event.source,
        bot_id=event.bot_id,
        symbol=event.symbol,
        magic_number=event.magic_number,
        event_type=event.event_type,
        image_base64=str(image_base64),
        time=event.time,
        side=event.side,
        lot=event.lot,
        entry=event.entry,
        sl=event.sl,
        tp1=event.tp1,
        tp2=event.tp2,
        tp3=event.tp3,
        profit=event.profit,
        balance=event.balance,
        equity=event.equity,
        ticket=event.ticket,
        trade_uid=event.trade_uid,
    )
    return save_native_screenshot_file(pseudo, str(event.event_type or "").strip().lower(), image_bytes)


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def bool_param(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@app.on_event("startup")
async def startup() -> None:
    global _daily_report_task, _bias_report_task
    init_db()
    load_symbols()
    _daily_report_task = asyncio.create_task(daily_report_loop())
    _bias_report_task = asyncio.create_task(bias_report_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _daily_report_task:
        _daily_report_task.cancel()
    if _bias_report_task:
        _bias_report_task.cancel()


# ── 1. Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "status": "running", "system_mode": config.SYSTEM_MODE}


# ── Dashboard static page ──────────────────────────────────────────────────────

@app.get("/dashboard")
async def serve_dashboard():
    if not DASHBOARD_FILE.exists():
        return JSONResponse({"error": "Dashboard not found"}, status_code=404)
    return FileResponse(
        DASHBOARD_FILE,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ── 2. Webhook ─────────────────────────────────────────────────────────────────

@app.post("/api/webhook/tradingview")
async def webhook_tradingview(request: Request):
    if config.is_native_mt5_only():
        logger.info("TradingView webhook disabled because SYSTEM_MODE=NATIVE_MT5_ONLY")
        return {
            "ok": False,
            "disabled": True,
            "reason": "TradingView bridge disabled. Native MT5 mode is active.",
        }

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
    if config.is_native_mt5_only():
        return {"ok": True, "commands": []}

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
    if config.is_native_mt5_only():
        return {
            "ok": True,
            "signal_id": body.signal_id,
            "status": "ignored",
            "disabled": True,
        }

    updated = q.acknowledge(body.signal_id)
    if not updated:
        return err(f"signal_id '{body.signal_id}' not found in status=sent")
    notify_event("ack_received", body.signal_id)
    return {"ok": True, "signal_id": body.signal_id, "status": "acknowledged"}


# ── 5. MT5 execution report ───────────────────────────────────────────────────

@app.post("/api/mt5/execution-report")
async def mt5_execution_report(report: ExecutionReport):
    q.save_execution_report(report)
    q.record_event(
        "execution_report_received",
        report.signal_id,
        {"status": report.status},
    )
    normalized_status = report.status.strip().lower()
    if should_notify_execution(normalized_status):
        notify_execution(normalized_status, report)
    return {"ok": True, "signal_id": report.signal_id}


@app.post("/api/mt5/account-snapshot")
async def mt5_account_snapshot(snapshot: AccountSnapshot):
    acct.save_account_snapshot(snapshot)
    return {"ok": True}


@app.post("/api/mt5/positions-snapshot")
async def mt5_positions_snapshot(snapshot: PositionsSnapshot):
    acct.save_positions_snapshot(snapshot)
    return {"ok": True, "positions": len(snapshot.positions)}


@app.post("/api/mt5/deal-report")
async def mt5_deal_report(report: DealReport):
    acct.save_deal_report(report)
    return {"ok": True, "deal_ticket": report.deal_ticket}


@app.post("/api/mt5/native-event")
async def mt5_native_event(event: NativeMT5Event, request: Request):
    if not native_secret_matches(event.secret, request):
        return err("Invalid secret", status=403)
    raw_payload = event.model_dump(mode="json", exclude={"secret"})
    normalized = normalizeNativeTradeEvent(raw_payload)
    saved = acct.save_native_event(event)
    notified = False
    if saved:
        payload = dict(raw_payload)
        payload["event_type"] = accounting_event_type(payload, normalized)
        payload["original_event_type"] = normalized["rawEventType"]
        payload["normalized_type"] = normalized["normalizedType"]
        payload["trade_uid"] = normalized["tradeUid"]
        journal = acct.get_native_trade_journal(normalized["tradeUid"]) or {}
        for key, value in journal.items():
            if value is not None and value != "":
                payload.setdefault(key, value)
        if normalized["telegramTemplate"] == "closed":
            try:
                daily_stats = acct.native_pnl_today()
            except Exception:
                daily_stats = None
        else:
            daily_stats = None
        if normalized["shouldNotifyTelegram"]:
            text = format_clean_trade_message(payload, normalized, daily_stats)
            if text:
                try:
                    screenshot_path = direct_screenshot_from_payload(event, payload)
                except ValueError:
                    screenshot_path = None
                if screenshot_path:
                    notified = send_telegram_photo(screenshot_path, telegram_caption(text))
                else:
                    key = pending_key(event.bot_id, event.symbol)
                    old = pending_messages.pop(key, None)
                    if old and old.get("task"):
                        old["task"].cancel()
                    task = asyncio.create_task(send_with_screenshot_timeout(event.bot_id, event.symbol))
                    pending_messages[key] = {
                        "text": text,
                        "event_type": str(event.event_type or "").strip().lower(),
                        "symbol": event.symbol,
                        "bot_id": event.bot_id,
                        "timestamp": time.time(),
                        "chat_id": config.TELEGRAM_ADMIN_CHAT_ID,
                        "task": task,
                    }
                    notified = True
                acct.mark_native_event_telegram_sent(normalized["eventId"], notified)
    return {
        "ok": True,
        "event_type": str(event.event_type or "").strip().lower(),
        "normalized_type": normalized["normalizedType"],
        "duplicate": not saved,
        "notified": notified,
    }


@app.post("/api/mt5/native-account")
async def mt5_native_account(snapshot: NativeMT5AccountSnapshot, request: Request):
    if not native_secret_matches(snapshot.secret, request):
        return err("Invalid secret", status=403)
    acct.save_native_account_snapshot(snapshot)
    return {"ok": True}


@app.post("/api/mt5/native-heartbeat")
async def mt5_native_heartbeat(heartbeat: NativeMT5Heartbeat, request: Request):
    if not native_secret_matches(heartbeat.secret, request):
        return err("Invalid secret", status=403)
    control = acct.save_native_heartbeat(heartbeat)
    enabled = bool(control.get("enabled", 1))
    return {
        "ok": True,
        "bot_id": heartbeat.bot_id,
        "enabled": enabled,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "control": {
            "enabled": enabled,
            "pause_new_entries": not enabled,
        },
    }


@app.get("/api/mt5/native-config")
async def mt5_native_config(bot_id: str, request: Request, secret: str | None = None):
    if not native_secret_matches(secret, request):
        return err("Invalid secret", status=403)
    control = acct.native_config(bot_id)
    return {
        "ok": True,
        "bot_id": control.get("bot_id"),
        "enabled": bool(control.get("enabled")),
        "symbol": control.get("symbol") or "",
        "reason": control.get("reason") or "",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "mode": config.SYSTEM_MODE,
    }


@app.get("/api/mt5/native-control")
async def mt5_native_control_get(bot_id: str, request: Request, secret: str | None = None):
    if not native_secret_matches(secret, request):
        return err("Invalid secret", status=403)
    control = acct.get_native_control(bot_id)
    return {"ok": True, **control}


@app.post("/api/mt5/native-control")
async def mt5_native_control_post(body: NativeMT5ControlRequest, request: Request):
    if not native_secret_matches(body.secret, request):
        return err("Invalid secret", status=403)
    control = acct.get_native_control(body.bot_id, body.symbol, body.magic_number)
    return {"ok": True, **control}


@app.get("/api/bots/status")
async def api_bots_status():
    controls = acct.list_native_bot_controls(include_defaults=True)
    return {
        "ok": True,
        "bots": [
            {
                "bot_id": control.get("bot_id"),
                "asset": control.get("asset"),
                "symbol": control.get("symbol"),
                "enabled": bool(control.get("enabled", 1)),
                "status": "ENABLED" if bool(control.get("enabled", 1)) else "DISABLED",
                "reason": control.get("paused_reason") or "",
                "online_status": control.get("online_status"),
                "last_heartbeat_at": control.get("last_heartbeat_at"),
            }
            for control in controls
        ],
    }


@app.post("/api/bots/enable")
async def api_bots_enable(body: BotControlRequest, request: Request):
    if not task_secret_matches(body.secret, request):
        return err("Invalid secret", status=403)
    control = acct.set_native_bot_enabled(body.bot_id, True, "")
    if not control:
        return err("Bot not found", status=404)
    return {"ok": True, "bot_id": control.get("bot_id"), "enabled": True}


@app.post("/api/bots/disable")
async def api_bots_disable(body: BotControlRequest, request: Request):
    if not task_secret_matches(body.secret, request):
        return err("Invalid secret", status=403)
    control = acct.set_native_bot_enabled(body.bot_id, False, body.reason or "api pause")
    if not control:
        return err("Bot not found", status=404)
    return {"ok": True, "bot_id": control.get("bot_id"), "enabled": False, "reason": control.get("paused_reason") or ""}


@app.post("/api/mt5/native-screenshot")
async def mt5_native_screenshot(screenshot: NativeMT5Screenshot, request: Request):
    if not native_secret_matches(screenshot.secret, request):
        return err("Invalid secret", status=403)

    event_type = str(screenshot.event_type or "").strip().lower()
    if event_type not in NATIVE_SCREENSHOT_EVENTS:
        return err(f"Screenshot event_type '{event_type}' is not allowed")

    try:
        image_bytes = decode_native_screenshot(screenshot.image_base64)
    except ValueError as exc:
        return err(str(exc))

    file_path = save_native_screenshot_file(screenshot, event_type, image_bytes)
    prune_native_screenshots()
    event = screenshot.model_dump(mode="json", exclude={"secret", "image_base64"})
    event["event_type"] = event_type
    normalized = normalizeNativeTradeEvent(event)
    key = pending_key(screenshot.bot_id, screenshot.symbol)
    pending = pending_messages.pop(key, None)
    if pending and pending.get("task"):
        pending["task"].cancel()
    caption = telegram_caption(pending["text"]) if pending else ""
    if not caption:
        if normalized["shouldNotifyTelegram"]:
            journal = acct.get_native_trade_journal(normalized["tradeUid"]) or {}
            payload = dict(event)
            for k, v in journal.items():
                if v is not None and v != "":
                    payload.setdefault(k, v)
            caption = format_clean_trade_message(payload, normalized)
        else:
            caption = ""
    caption = telegram_caption(caption)
    acct.save_native_screenshot_record(screenshot, str(file_path), caption)
    prune_native_screenshot_records()
    if not caption or not normalized["shouldNotifyTelegram"]:
        return {"ok": True, "event_type": event_type, "sent": False, "suppressed": True}
    sent = send_telegram_photo(file_path, caption)
    if not sent:
        return JSONResponse({"ok": False, "error": "Telegram sendPhoto failed"}, status_code=502)
    return {"ok": True, "event_type": event_type, "sent": True}


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return err("Invalid JSON body")

    if handle_telegram_update(update):
        return {"ok": True, "handled": True}

    chat_id, text = parse_telegram_update(update)
    if not chat_id or not text:
        return {"ok": True, "handled": False}

    if config.TELEGRAM_ADMIN_CHAT_ID and chat_id != config.TELEGRAM_ADMIN_CHAT_ID:
        return err("Unauthorized chat", status=403)

    response = handle_command(text, chat_id)
    send_telegram_message(response)
    return {"ok": True, "handled": True}


@app.post("/api/tasks/daily-report")
async def api_daily_report_task(body: DailyReportTaskRequest, request: Request):
    if not task_secret_matches(body.secret, request):
        return err("Invalid secret", status=403)
    sent, reason, berlin_day = send_daily_report_if_due(force=body.force)
    return {"ok": True, "sent": sent, "reason": reason, "berlin_day": berlin_day}


@app.post("/api/bias/run")
async def api_bias_run(body: dict, request: Request):
    send = bool(body.get("send")) if isinstance(body, dict) else False
    allow_network = bool(body.get("allow_network", True)) if isinstance(body, dict) else True
    if (send or allow_network) and not task_secret_matches(str(body.get("secret") or ""), request):
        return err("Invalid secret", status=403)
    try:
        report = calculate_bias_report(allow_network=allow_network)
        bias_store.save_bias_report(report)
        sent = send_telegram_message(report["telegram_text"]) if send else False
    except Exception as exc:
        logger.exception("Bias run failed")
        return err(f"Bias run failed: {exc}", status=500)
    return {"ok": True, "sent": sent, "report": _bias_public_payload(report)}


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


@app.get("/api/account")
async def api_account():
    return {"ok": True, "account": acct.latest_account_snapshot()}


@app.get("/api/positions")
async def api_positions():
    return {"ok": True, "positions": acct.current_positions()}


@app.get("/api/trades/today")
async def api_trades_today():
    return {"ok": True, "trades": acct.trades_today()}


@app.get("/api/pnl/today")
async def api_pnl_today():
    return {"ok": True, "pnl": acct.pnl_today()}


# ── 10. Dashboard — public, no auth, CORS * ────────────────────────────────────

@app.get("/api/dashboard/status")
async def dashboard_status():
    try:
        heartbeat = acct.last_mt5_heartbeat()
        bots = acct.list_native_bot_controls(include_defaults=True)
        return {
            "ok": True,
            "system_mode": config.SYSTEM_MODE,
            **config.db_file_diagnostics(),
            "trading_enabled": bool(get_setting("trading_enabled", config.TRADING_ENABLED)),
            "last_heartbeat_at": heartbeat,
            "bots_total": len(bots),
            "bots_online": sum(1 for b in bots if b.get("online_status") == "online"),
            "bots_enabled": sum(1 for b in bots if bool(b.get("enabled", 1))),
        }
    except Exception:
        return {
            "ok": True,
            "system_mode": config.SYSTEM_MODE,
            **config.db_file_diagnostics(),
            "trading_enabled": None,
            "last_heartbeat_at": None,
            "bots_total": 0,
            "bots_online": 0,
            "bots_enabled": 0,
        }


@app.get("/api/dashboard/storage-health")
async def dashboard_storage_health():
    try:
        return acct.storage_health()
    except Exception as exc:
        logger.exception("Failed to load storage health")
        return {"ok": False, "error": f"storage_health_unavailable: {exc}"}


@app.get("/api/dashboard/account")
async def dashboard_account():
    try:
        account = acct.latest_account_snapshot()
    except Exception:
        account = None
    return {"ok": True, "account": account}


@app.get("/api/dashboard/positions")
async def dashboard_positions():
    try:
        positions = acct.current_positions()
    except Exception:
        positions = []
    return {"ok": True, "positions": positions}


@app.get("/api/dashboard/trades")
async def dashboard_trades(source: str = "bot", period: str = "all", asset: str = "ALL", limit: int = 50, offset: int = 0, bot_id: str | None = None):
    try:
        selector = bot_id or asset
        trades = acct.get_trades_filtered(source=source, period=period, asset=selector or "ALL", limit=min(limit, 500), offset=offset)
        total = len(acct.get_trades_filtered(source=source, period=period, asset=selector or "ALL", limit=10000, offset=0))
    except Exception:
        trades = []
        total = 0
    return {"ok": True, "source": source, "period": period, "asset": asset, "trades": trades, "total": total}


@app.get("/api/dashboard/summary")
async def dashboard_summary(period: str = "today", asset: str = "ALL"):
    try:
        return {
            "ok": True,
            "status": await dashboard_status(),
            "account": (await dashboard_account()).get("account"),
            "pnl": (await dashboard_pnl(period)).get("pnl"),
            "stats": (await dashboard_stats(source="bot", period=period, asset=asset)).get("stats"),
            "bots": (await dashboard_bots()).get("bots"),
        }
    except Exception:
        logger.exception("Failed to load dashboard summary")
        return {"ok": False, "error": "summary_unavailable"}


@app.get("/api/dashboard/trade/{trade_uid}")
async def dashboard_trade(trade_uid: str):
    try:
        trade = acct.get_native_trade_journal(trade_uid) or acct.get_journal_trade(trade_uid)
    except Exception:
        logger.exception("Failed to load dashboard trade")
        trade = None
    return {"ok": True, "trade": trade}


@app.get("/api/dashboard/events")
async def dashboard_events(limit: int = 100, bot_id: str | None = None):
    try:
        events = acct.native_trade_events(limit=min(limit, 500), selector=bot_id)
    except Exception:
        logger.exception("Failed to load dashboard events")
        events = []
    return {"ok": True, "events": events}


@app.post("/api/mt5/native-backtest")
async def mt5_native_backtest(body: dict, request: Request):
    if not native_secret_matches(str(body.get("secret") or ""), request):
        return err("Invalid secret", status=403)
    bot_id = str(body.get("bot_id") or "").strip()
    trades = body.get("trades") or []
    if not bot_id:
        return err("bot_id is required")
    if not isinstance(trades, list):
        return err("trades must be a list")
    try:
        logger.info("native-backtest received bot_id=%s trades=%s", bot_id, len(trades))
        saved = acct.save_backtest_trades(bot_id, trades)
        logger.info("native-backtest saved bot_id=%s saved=%s", bot_id, saved)
    except Exception as exc:
        logger.exception("Failed to save native backtest trades")
        return err(f"Failed to save backtest trades: {exc}", status=500)
    return {"ok": True, "status": "ok", "bot_id": bot_id, "received": len(trades), "saved": saved}


@app.post("/api/mt5/native-history")
async def mt5_native_history(body: dict, request: Request):
    if not native_secret_matches(str(body.get("secret") or ""), request):
        return err("Invalid secret", status=403)
    bot_id = str(body.get("bot_id") or "").strip()
    deals = body.get("deals") or []
    if not bot_id:
        return err("bot_id is required")
    if not isinstance(deals, list):
        return err("deals must be a list")
    try:
        logger.info("native-history received bot_id=%s deals=%s", bot_id, len(deals))
        saved = acct.save_history_deals(bot_id, deals)
        logger.info("native-history saved bot_id=%s saved=%s", bot_id, saved)
    except Exception as exc:
        logger.exception("Failed to save native history deals")
        return err(f"Failed to save history deals: {exc}", status=500)
    return {"ok": True, "status": "ok", "bot_id": bot_id, "received": len(deals), "saved": saved}


@app.post("/api/history/import")
async def api_history_import(
    request: Request,
    dry_run: bool = True,
    source_name: str | None = None,
    dedupe: bool = True,
    bot_id: str | None = None,
):
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        rows, input_format = parse_history_payload(raw_body, content_type)
    except Exception as exc:
        return err(f"Invalid history import payload: {exc}", status=400)
    body_secret = ""
    if "json" in content_type.lower() and raw_body:
        try:
            parsed_body = json.loads(raw_body.decode("utf-8-sig", errors="replace"))
        except json.JSONDecodeError:
            parsed_body = None
        if isinstance(parsed_body, dict):
            dry_run = bool_param(parsed_body.get("dry_run"), dry_run)
            source_name = parsed_body.get("source_name") or source_name
            dedupe = bool_param(parsed_body.get("dedupe"), dedupe)
            bot_id = parsed_body.get("bot_id") or bot_id
            body_secret = str(parsed_body.get("secret") or "")
    if not dry_run and not task_secret_matches(body_secret, request):
        return err("Invalid secret", status=403)
    result = import_history_rows(
        rows,
        dry_run=dry_run,
        source_name=source_name,
        dedupe=dedupe,
        bot_id=bot_id,
    )
    result["input_format"] = input_format
    return result


@app.get("/api/dashboard/backtest")
async def dashboard_backtest(bot_id: str | None = None, asset: str | None = None, limit: int = 500):
    try:
        selector = bot_id or asset
        trades = acct.get_trades_filtered(source="backtest", period="all", asset=selector or "ALL", limit=min(limit, 1000), offset=0)
        summary = acct.backtest_summary(bot_id=selector)
    except Exception:
        logger.exception("Failed to load dashboard backtest")
        trades = []
        summary = {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "best_trade": None,
            "worst_trade": None,
            "profit_factor": 0,
            "avg_r": None,
            "tp1_hit_rate": 0,
            "tp2_hit_rate": 0,
        }
    return {"ok": True, "bot_id": bot_id, "asset": asset, "summary": summary, "trades": trades}


@app.get("/api/dashboard/stats")
async def dashboard_stats(source: str = "bot", period: str = "all", asset: str = "ALL"):
    try:
        stats = acct.get_stats_filtered(source=source, period=period, asset=asset)
    except Exception:
        stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "best_trade": None,
            "worst_trade": None,
            "avg_trade": None,
            "gross_profit": 0,
            "gross_loss": 0,
            "profit_factor": 0,
            "avg_r": None,
            "max_r": None,
            "tp1_hit_rate": 0,
            "tp2_hit_rate": 0,
            "source": source,
            "period": period,
            "asset": asset,
        }
    return {"ok": True, "stats": stats}


@app.get("/api/dashboard/bias")
async def dashboard_bias():
    try:
        report = bias_store.latest_bias_report()
    except Exception:
        logger.exception("Failed to load bias report")
        report = None
    return {"ok": True, "bias": _bias_public_payload(report) if report else None}


@app.get("/api/dashboard/strategy-lab")
async def dashboard_strategy_lab(symbol: str | None = None, bot_id: str | None = None):
    try:
        return build_strategy_lab_report(symbol=symbol, bot_id=bot_id, include_bias_filter=True)
    except Exception as exc:
        logger.exception("Failed to load strategy lab")
        return {"ok": False, "error": f"strategy_lab_unavailable: {exc}"}


@app.get("/api/dashboard/strategy-lab/data-health")
async def dashboard_strategy_lab_data_health(symbol: str | None = None, bot_id: str | None = None):
    try:
        report = build_strategy_lab_report(symbol=symbol, bot_id=bot_id, include_bias_filter=False)
        storage = acct.storage_health()
        return {
            "ok": True,
            "trade_count": report.get("trade_count", 0),
            "sample_warning": report.get("sample_warning"),
            "data_sources": report.get("data_sources", []),
            "storage_health": storage,
        }
    except Exception as exc:
        logger.exception("Failed to load strategy lab data health")
        return {"ok": False, "error": f"strategy_lab_data_health_unavailable: {exc}"}


@app.post("/api/strategy-lab/run")
async def api_strategy_lab_run(body: dict):
    payload = body or {}
    if payload.get("dry_run") is False:
        return err("dry_run=false is not supported. Strategy lab is simulation-only.", status=400)
    try:
        return run_strategy_lab(
            symbol=payload.get("symbol"),
            bot_id=payload.get("bot_id"),
            dry_run=bool(payload.get("dry_run", True)),
            optimize=bool(payload.get("optimize", True)),
            include_bias_filter=bool(payload.get("include_bias_filter", False)),
        )
    except Exception as exc:
        logger.exception("Strategy lab run failed")
        return {"ok": False, "error": f"strategy_lab_run_failed: {exc}"}


@app.get("/api/dashboard/strategy-lab/recommendations")
async def dashboard_strategy_lab_recommendations(symbol: str | None = None, bot_id: str | None = None):
    try:
        return recommendations_payload(symbol=symbol, bot_id=bot_id)
    except Exception as exc:
        logger.exception("Failed to load strategy lab recommendations")
        return {"ok": False, "error": f"strategy_lab_recommendations_unavailable: {exc}"}


@app.get("/api/dashboard/pnl")
async def dashboard_pnl(period: str = "today"):
    try:
        if period in ("today", "day", "1d"):
            pnl = acct.native_pnl_today()
        else:
            trades = acct.native_closed_trades(period=period, limit=10000)
            profits = [float(t.get("profit") or 0) for t in trades]
            wins = [p for p in profits if p > 0]
            losses_list = [p for p in profits if p < 0]
            closed_pnl = round(sum(profits), 2)
            pnl = {
                "trades_count": len(trades),
                "wins": len(wins),
                "losses": len(losses_list),
                "closed_pnl": closed_pnl,
                "net_pnl": closed_pnl,
                "best_trade": round(max(profits), 2) if profits else None,
                "worst_trade": round(min(profits), 2) if profits else None,
            }
    except Exception:
        pnl = {}
    return {"ok": True, "period": period, "pnl": pnl}


@app.get("/api/dashboard/bots")
async def dashboard_bots():
    try:
        bots = acct.list_native_bot_controls(include_defaults=True)
        result = [
            {
                "bot_id": b.get("bot_id"),
                "asset": b.get("asset"),
                "symbol": b.get("symbol"),
                "enabled": bool(b.get("enabled", 1)),
                "status": "ENABLED" if bool(b.get("enabled", 1)) else "DISABLED",
                "online_status": b.get("online_status"),
                "last_heartbeat_at": b.get("last_heartbeat_at"),
                "last_event_at": b.get("last_event_at"),
                "last_event_type": b.get("last_event_type"),
                "has_position": bool(b.get("has_position")),
                "active_position": b.get("active_position"),
                "reason": b.get("paused_reason") or "",
            }
            for b in bots
        ]
    except Exception:
        result = []
    return {"ok": True, "bots": result}


@app.get("/api/dashboard/journal")
async def dashboard_journal(period: str = "today", bot_id: str | None = None, limit: int = 50):
    try:
        entries = acct.journal_entries(period=period, selector=bot_id, limit=min(limit, 500))
    except Exception:
        entries = []
    return {"ok": True, "period": period, "journal": entries}


@app.get("/api/dashboard/screenshots")
async def dashboard_screenshots(bot_id: str | None = None, limit: int = 20):
    try:
        screenshots = acct.native_screenshots(selector=bot_id, limit=limit)
        for shot in screenshots:
            shot["url"] = f"/api/dashboard/screenshots/{shot['id']}/image"
    except Exception:
        logger.exception("Failed to load dashboard screenshots")
        screenshots = []
    return {"ok": True, "screenshots": screenshots}


@app.get("/api/dashboard/screenshots/{screenshot_id}/image")
async def dashboard_screenshot_image(screenshot_id: int):
    try:
        screenshot = acct.native_screenshot_file(screenshot_id)
    except Exception:
        logger.exception("Failed to load dashboard screenshot file")
        screenshot = None
    if not screenshot:
        return JSONResponse({"ok": False, "error": "screenshot_not_found"}, status_code=404)
    path = Path(str(screenshot.get("file_path") or ""))
    try:
        resolved = path.resolve()
        allowed = (Path("data") / "screenshots").resolve()
        if allowed not in resolved.parents and resolved != allowed:
            return JSONResponse({"ok": False, "error": "screenshot_path_not_allowed"}, status_code=403)
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_screenshot_path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"ok": False, "error": "screenshot_file_missing"}, status_code=404)
    return FileResponse(resolved)


@app.get("/api/dashboard/performance")
async def dashboard_performance(period: str = "today", bot_id: str | None = None):
    try:
        summary = acct.performance_summary(period=period, selector=bot_id)
    except Exception:
        summary = {"period": period, "items": [], "totals": {}}
    return {"ok": True, "performance": summary}


async def daily_report_loop() -> None:
    while True:
        try:
            send_daily_report_if_due(force=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily report loop failed")
        await asyncio.sleep(60)


def send_daily_report_if_due(force: bool = False) -> tuple[bool, str, str]:
    now = datetime.now(BERLIN_TZ)
    today_key = now.strftime("%Y-%m-%d")
    state_key = "last_daily_report_date"
    if not force and now.hour != 21:
        return False, "not_due", today_key
    if not force and acct.state_get(state_key) == today_key:
        return False, "already_sent", today_key
    text = format_daily_report()
    sent = send_telegram_message(text)
    if sent:
        acct.state_set(state_key, today_key)
        return True, "sent", today_key
    return False, "telegram_send_failed", today_key


async def bias_report_loop() -> None:
    while True:
        try:
            send_bias_report_if_due(force=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Bias report loop failed")
        await asyncio.sleep(60)


def send_bias_report_if_due(force: bool = False) -> tuple[bool, str, str]:
    now_ny = datetime.now(NY_TZ)
    today_key = now_ny.strftime("%Y-%m-%d")
    state_key = "last_bias_report_date"
    if not force:
        if now_ny.weekday() >= 5:
            return False, "weekend", today_key
        if not (now_ny.hour == 9 and now_ny.minute >= 20):
            return False, "not_due", today_key
        if acct.state_get(state_key) == today_key:
            return False, "already_sent", today_key
    report = calculate_bias_report(allow_network=True)
    bias_store.save_bias_report(report)
    sent = send_telegram_message(report["telegram_text"])
    if sent:
        acct.state_set(state_key, today_key)
        return True, "sent", today_key
    return False, "telegram_send_failed", today_key


def _bias_public_payload(report: dict | None) -> dict | None:
    if not report:
        return None
    return {
        "id": report.get("id"),
        "report_date": report.get("report_date"),
        "run_at": report.get("run_at"),
        "ny_time": report.get("ny_time"),
        "berlin_time": report.get("berlin_time"),
        "macro_risk": report.get("macro_risk"),
        "data_quality_score": report.get("data_quality_score"),
        "source_availability": report.get("source_availability") or {},
        "source_details": report.get("source_details") or {},
        "symbols": report.get("symbols") or [],
        "telegram_text": report.get("telegram_text"),
    }


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


def native_secret_matches(body_secret: str | None, request: Request) -> bool:
    expected = config.MT5_NATIVE_SECRET or config.WEBHOOK_SECRET
    return (
        bool(expected)
        and (
            body_secret == expected
            or request.headers.get("x-mt5-native-secret", "") == expected
            or request.headers.get("x-webhook-secret", "") == expected
        )
    )


def task_secret_matches(body_secret: str | None, request: Request) -> bool:
    expected = config.TASK_SECRET or config.MT5_NATIVE_SECRET or config.WEBHOOK_SECRET
    return (
        bool(expected)
        and (
            body_secret == expected
            or request.headers.get("x-task-secret", "") == expected
            or request.headers.get("x-mt5-native-secret", "") == expected
            or request.headers.get("x-webhook-secret", "") == expected
        )
    )


def decode_native_screenshot(image_base64: str) -> bytes:
    data = str(image_base64 or "").strip()
    if not data:
        raise ValueError("image_base64 is required")
    if data.lower().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    data = "".join(data.split())
    try:
        image_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid base64 image data")
    if not image_bytes:
        raise ValueError("Decoded image is empty")
    if len(image_bytes) > MAX_NATIVE_SCREENSHOT_BYTES:
        raise ValueError("Screenshot image too large. Max size is 10 MB.")
    return image_bytes


def save_native_screenshot_file(screenshot: NativeMT5Screenshot, event_type: str, image_bytes: bytes) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    symbol = safe_filename_part(screenshot.symbol or "unknown")
    bot_id = safe_filename_part(screenshot.bot_id or "native")
    filename = f"{timestamp}_{event_type}_{symbol}_{bot_id}_{uuid4().hex[:10]}.png"
    file_path = SCREENSHOT_DIR / filename
    file_path.write_bytes(image_bytes)
    return file_path


def prune_native_screenshots() -> None:
    if not SCREENSHOT_DIR.exists():
        return
    files = sorted(
        [path for path in SCREENSHOT_DIR.glob("*.png") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_file in files[SCREENSHOTS_TO_KEEP:]:
        try:
            old_file.unlink()
        except OSError:
            logger.info("Could not remove old native screenshot file")


def prune_native_screenshot_records() -> None:
    for deleted_path in acct.prune_native_screenshot_records(SCREENSHOTS_TO_KEEP_PER_BOT):
        try:
            path = Path(deleted_path)
            if path.exists():
                path.unlink()
        except OSError:
            logger.info("Could not remove old native screenshot record file")


def safe_filename_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in str(value))
    return safe[:48] or "unknown"
