import asyncio
import base64
import binascii
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config
from . import account_store as acct
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
from .validators import validate_signal
from . import queue as q
from .symbol_mapper import load_symbols
from .telegram_bot import (
    handle_command,
    handle_telegram_update,
    format_daily_report,
    format_native_screenshot_caption,
    notify_close_signal,
    notify_event,
    notify_execution,
    notify_native_event,
    parse_telegram_update,
    send_telegram_photo,
    send_telegram_message,
    should_notify_execution,
    validate_change,
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
    "tp1_closed",
    "tp2_closed",
    "tp3_closed",
    "be_moved",
    "position_closed",
    "closed_by_signal",
}
MAX_NATIVE_SCREENSHOT_BYTES = 10 * 1024 * 1024
SCREENSHOT_DIR = Path("data") / "screenshots"
SCREENSHOTS_TO_KEEP = 100
SCREENSHOTS_TO_KEEP_PER_BOT = 20
BERLIN_TZ = ZoneInfo("Europe/Berlin")
_daily_report_task: asyncio.Task | None = None


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


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


@app.on_event("startup")
async def startup() -> None:
    global _daily_report_task
    init_db()
    load_symbols()
    _daily_report_task = asyncio.create_task(daily_report_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _daily_report_task:
        _daily_report_task.cancel()


# ── 1. Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "status": "running", "system_mode": config.SYSTEM_MODE}


# ── Dashboard static page ──────────────────────────────────────────────────────

@app.get("/dashboard")
async def serve_dashboard():
    if not DASHBOARD_FILE.exists():
        return JSONResponse({"error": "Dashboard not found"}, status_code=404)
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


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
    saved = acct.save_native_event(event)
    notified = notify_native_event(event) if saved else False
    return {
        "ok": True,
        "event_type": str(event.event_type or "").strip().lower(),
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
    caption = format_native_screenshot_caption(event)
    acct.save_native_screenshot_record(screenshot, str(file_path), caption)
    prune_native_screenshot_records()
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
            "trading_enabled": None,
            "last_heartbeat_at": None,
            "bots_total": 0,
            "bots_online": 0,
            "bots_enabled": 0,
        }


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
async def dashboard_trades(period: str = "today", bot_id: str | None = None, limit: int = 50):
    try:
        trades = acct.native_closed_trades(period=period, selector=bot_id, limit=min(limit, 500))
    except Exception:
        trades = []
    return {"ok": True, "period": period, "trades": trades}


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
