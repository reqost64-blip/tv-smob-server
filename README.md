# Native MT5 Notification Server

FastAPI notification, dashboard, and journal server for native MT5 Expert
Advisors.

Default mode is `NATIVE_MT5_ONLY`: MT5 EAs analyze the market, open trades, and
manage TP/BE/SL locally. Render is used only for Telegram notifications,
dashboard commands, account snapshots, trade history, and AI market research.

Legacy mode `TRADINGVIEW_BRIDGE` is still supported for old deployments, but it
is not the default.

## Architecture

```text
Native MT5 Expert Advisor
      | POST /api/mt5/native-event
      | POST /api/mt5/native-account
      v
FastAPI server on Render
      | SQLite journal / dashboard state
      v
Telegram bot notifications and commands
```

In `NATIVE_MT5_ONLY`, the old TradingView command queue is disabled:

- `POST /api/webhook/tradingview` returns a disabled response and does not queue commands.
- `GET /api/mt5/commands` returns `commands: []`.
- `POST /api/mt5/ack` is accepted but ignored without Telegram noise.

## Quick Start

```bash
cd C:\Projects\tv-mt5-bridge
python -m venv server/.venv
server/.venv/Scripts/activate
pip install -r server/requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

## Environment

Create `.env` from `.env.example`.

```text
WEBHOOK_SECRET=your-secret-here
SYSTEM_MODE=NATIVE_MT5_ONLY
MT5_NATIVE_SECRET=optional-native-secret
DB_FILE=bridge.db
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-telegram-admin-chat-id
TRADING_ENABLED=true
OPENAI_API_KEY=optional-openai-key
OPENAI_MODEL=gpt-5.5
OPENAI_TIMEOUT_SECONDS=60
ENABLE_AI_WEB_SEARCH=true
```

If `MT5_NATIVE_SECRET` is not set, native MT5 endpoints use `WEBHOOK_SECRET`.
Do not print or commit real secrets.

Do not commit real Telegram tokens, webhook secrets, MT5 passwords, or OpenAI
API keys.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/webhook/tradingview` | Legacy TradingView signal; disabled in `NATIVE_MT5_ONLY` |
| GET | `/api/mt5/commands` | Legacy MT5 command polling; returns `commands: []` in `NATIVE_MT5_ONLY` |
| POST | `/api/mt5/ack` | Legacy ack; accepted and ignored in `NATIVE_MT5_ONLY` |
| POST | `/api/mt5/native-event` | Native MT5 EA trade event |
| POST | `/api/mt5/native-account` | Native MT5 EA account snapshot |
| POST | `/api/mt5/native-heartbeat` | Native MT5 EA heartbeat and control sync |
| GET | `/api/mt5/native-control?bot_id=...&secret=...` | Remote control state for an EA |
| POST | `/api/mt5/native-control` | Remote control state for an EA |
| POST | `/api/mt5/execution-report` | Legacy MT5 execution status |
| POST | `/api/mt5/account-snapshot` | MT5 posts account snapshot |
| POST | `/api/mt5/positions-snapshot` | MT5 posts open positions |
| POST | `/api/mt5/deal-report` | MT5 posts closed deal |
| POST | `/api/telegram/webhook` | Telegram bot webhook |
| GET | `/api/settings` | Current server settings |
| POST | `/api/settings` | Update setting with `WEBHOOK_SECRET` |
| GET | `/api/audit-log` | Approval and audit history |
| GET | `/api/account` | Latest account snapshot |
| GET | `/api/positions` | Current open positions |
| GET | `/api/trades/today` | Today's deals |
| GET | `/api/pnl/today` | Today's PnL summary |

## Native MT5 Control Center

`NATIVE_MT5_ONLY` now includes a Telegram control center for native MT5 bots.
The server stores one `native_bot_controls` record per bot with `bot_id`,
`symbol`, `magic_number`, enabled/disabled state, heartbeat timestamps, last
account/event/screenshot timestamps, and a settings summary if the EA sends it.

Default behavior is conservative:

- Unknown bots are treated as `enabled=true`.
- The first `native-account`, `native-event`, or `native-heartbeat` creates the bot control row.
- Disabling a bot means `pause_new_entries=true`: no new entries only.
- Open positions are still managed by the EA: TP, SL, BE, partial closes, and invalid exits continue.
- Account snapshots do not auto-notify Telegram.
- The old TradingView queue remains disabled in `NATIVE_MT5_ONLY`; `/api/mt5/commands` is not used for trading.

Heartbeat example:

```json
{
  "secret": "your-secret-here",
  "source": "mt5_native",
  "bot_id": "NAS100_ORB_VWAP_RSI_OF",
  "symbol": "NAS100.r",
  "magic_number": 26043001,
  "status": "running",
  "has_position": false,
  "account_balance": 500.0,
  "account_equity": 500.0,
  "time": "2026-05-08T19:00:00Z"
}
```

The heartbeat response includes:

```json
{
  "ok": true,
  "bot_id": "NAS100_ORB_VWAP_RSI_OF",
  "enabled": true,
  "server_time": "2026-05-08T19:00:00+00:00",
  "control": {
    "enabled": true,
    "pause_new_entries": false
  }
}
```

## Telegram Bot Controls

Control commands:

```text
/bots
/bot NAS100
/enable NAS100
/disable NAS100
/enable_all
/disable_all
```

The main keyboard includes:

```text
Статус
Сделки
⚙️ Управление
Статистика
Последний скрин
⚙️ Настройки
Новости
```

`/disable NAS100` sets `enabled=false` for that bot and the EA receives
`pause_new_entries=true` from `/api/mt5/native-control`. It does not close any
position.

## Performance Control

Commands:

```text
/performance
/performance NAS100
/performance 7d
/performance 30d
/performance_all
/symbols
```

Performance is calculated from native journal and native event storage. Closed
PnL only uses final close events: `position_closed` and `closed_by_signal`.
`tp1_closed`, `tp2_closed`, and `tp3_closed` are counted as TP events but are
not added to closed PnL, which prevents duplicate realized PnL. Floating PnL
comes from active native trades.

Status rules:

- `closed_pnl > 0` and `profit_factor >= 1.2`: working asset.
- `closed_pnl < 0` and at least five trades: weak asset.
- execution errors: execution warning.
- fewer than three trades: low data.

## Trade Journal

The server maintains:

```text
native_trade_journal
native_trade_events
native_screenshots
```

Commands:

```text
/journal
/journal today
/journal 7d
/trade_last
/trade <id>
```

Native event handling:

- `opened`: creates/updates a journal trade.
- `tp1_closed`, `tp2_closed`, `tp3_closed`: mark TP flags.
- `be_moved`: marks BE.
- `position_closed`, `closed_by_signal`: close the journal trade and store final profit.
- `open_failed`, `close_failed`: stored as journal events.

If the EA sends `trade_uid`, the server uses it. Otherwise it derives one from
`bot_id`, `symbol`, `magic_number`, ticket if available, and open time.

## Last Screenshot

Commands:

```text
/last_screenshot
/last_screenshot NAS100
/screenshot NAS100
```

Screenshots posted through `/api/mt5/native-screenshot` are stored in
`native_screenshots` and can be retrieved later from Telegram. Captions are
plain text.

## Daily Report 21:00 Berlin

The FastAPI app starts an internal background task on Render. It checks once per
minute and sends the daily report once per Europe/Berlin date at 21:00. The
state key `last_daily_report_date` prevents duplicate sends.

Manual test command:

```text
/daily_report_now
```

If there is no native data, the report says that no native MT5 bot data is
available for the day.

## EA Remote Control Polling

Native EAs should poll:

```text
POST /api/mt5/native-control
POST /api/mt5/native-heartbeat
```

Required EA behavior:

- Poll remote control every `InpControlPollSeconds`.
- Send heartbeat every `InpHeartbeatSeconds`.
- If control returns `enabled=false` or `pause_new_entries=true`, block only new entries.
- Keep managing open positions: TP/SL/BE and partial exits must continue.
- Do not print the native secret in logs.

## Native MT5 Event Contract

Native EAs post trade lifecycle events to:

```text
POST /api/mt5/native-event
```

Example:

```json
{
  "secret": "your-secret-here",
  "source": "mt5_native",
  "bot_id": "NAS100_ORB_VWAP_RSI_OF",
  "symbol": "NAS100.r",
  "magic_number": 26043001,
  "event_type": "opened",
  "side": "sell",
  "lot": 0.01,
  "entry": 27600.0,
  "sl": 27620.0,
  "tp1": 27591.0,
  "tp2": 27560.0,
  "tp3": null,
  "profit": 0.0,
  "balance": 500.0,
  "equity": 500.0,
  "time": "2026-05-07T15:30:00Z",
  "message": "optional text"
}
```

The server stores every native event without `secret`. `opened` creates or
updates the active trade, `tp1_closed` / `tp2_closed` / `tp3_closed` /
`be_moved` update it, and `position_closed` / `closed_by_signal` close it and
write final PnL to the native trade history.

Automatic Telegram notifications are sent only for:

```text
opened
tp1_closed
tp2_closed
tp3_closed
be_moved
position_closed
closed_by_signal
open_failed
close_failed
error
```

The following event types are stored but are not pushed automatically:

```text
heartbeat
account_snapshot
positions_snapshot
status
debug
ack
command_poll
settings_changed
```

Account snapshots are posted to:

```text
POST /api/mt5/native-account
```

Example:

```json
{
  "secret": "your-secret-here",
  "source": "mt5_native",
  "symbol": "NAS100.r",
  "magic_number": 26043001,
  "balance": 500.0,
  "equity": 500.0,
  "margin": 0.0,
  "free_margin": 500.0,
  "open_positions": 1,
  "time": "2026-05-07T15:30:00Z"
}
```

`/status`, `/account`, `/positions`, `/trades`, `/history_today`, and
`/pnl_today` read the latest native MT5 data in `NATIVE_MT5_ONLY`. If the EA has
not sent data yet, Telegram replies:

```text
Данных от native MT5 bot пока нет.
```

## Russian Telegram Dashboard

Telegram UI is styled as a compact Russian AI trading dashboard. This is a
UI/UX-only layer: Pine Script, MQL5 EA, trading execution, and risk logic are not
changed by the dashboard styling.

The bot uses plain text with emojis and short dividers. It avoids fragile
MarkdownV2 and removes long raw URLs from market research answers. Sources are
shown as short names at the end.

Main keyboard buttons:

| Button | Action |
|--------|--------|
| `Статус` | `/status` |
| `Сделки` | `/trades` |
| `Новости` | `/market_today` |
| `⚙️ Управление` | `/settings` |

Legacy labels are still supported:

```text
Core Status
Trade Center
Market Intel
Control Panel
📊 Core Status
📈 Trade Center
📰 Market Intel
⚙️ Control Panel
```

Supported Telegram commands:

| Command | Description |
|---------|-------------|
| `/start` | Главное меню AI TRADING CONTROL |
| `/status` | Главный экран: сервер, MT5, счёт, PnL, очередь |
| `/last_trade` | Последний отчёт исполнения |
| `/today` | Сигналы и открытия сегодня |
| `/account` | MT5 account matrix |
| `/balance` | Баланс |
| `/equity` | Equity |
| `/positions` | Открытые позиции |
| `/trades` | Сделки сегодня |
| `/history_today` | Статистика дня |
| `/pnl_today` | PnL за день |
| `/news` | Рыночные новости |
| `/calendar` | Экономический календарь |
| `/market_today` | Риск-обзор рынка |
| `/ask <question>` | AI market/research вопрос |
| `/settings` | Панель управления |
| `/risk` | Risk matrix |
| `/approvals` | Pending approvals |
| `/confirm <approval_id>` | Применить pending approval |
| `/reject <approval_id>` | Отклонить pending approval |
| `/pause` | Создать approval на остановку новых входов |
| `/resume` | Создать approval на включение новых входов |
| `/dryrun_on` | Создать approval на включение DryRun |
| `/dryrun_off` | Создать approval на выключение DryRun, если `ALLOW_REAL_TRADING=true` |
| `/help` | Список команд |

Natural-language questions are supported in Russian without `/ask`:

```text
что сегодня важно по рынку
почему NAS100 падает сегодня
что влияет на золото
какие новости по биткоину
покажи статус
покажи сделки
какой баланс
что по XAUUSD сегодня
объясни безубыток
почему сделка закрылась
```

Risk-changing phrases do not apply changes immediately. They create a pending
approval first:

```text
поставь лот 0.02 на nas100
повысь лот на 20 процентов на nas100
уменьши лот на 30 процентов на btcusd
останови торговлю
включи торговлю
останови торговлю по NAS100 на 30 минут
```

Example dashboard message:

```text
⚡ AI TRADING CORE
━━━━━━━━━━━━━━━━━━

▌ СИСТЕМА
Сервер:  🟢 ONLINE
MT5:  🟢 ACTIVE
Торговля:  ENABLED
DryRun:  ON

▌ СЧЁТ
Баланс: 10 000.00 USD
Equity: 10 042.50 USD
PnL сегодня: +42.50 USD

▌ ИСПОЛНЕНИЕ
Открытых позиций: 2
Команд в очереди: 0
MT5 heartbeat: 12 сек назад

▌ АКТИВЫ
XAUUSD · NAS100 · DJ30 · US500 · BTCUSD
━━━━━━━━━━━━━━━━━━
Обновлено: 15:42 Berlin
```

Example pending approval:

```text
🧾 PENDING APPROVAL
━━━━━━━━━━━━━━━━━━

Параметр: symbol_lot_multiplier_NAS100
Сейчас: 1.0
Новое: 1.2
Approval ID: abc123def0

Применить:
/confirm abc123def0

Отклонить:
/reject abc123def0
```

Telegram webhook:

```text
https://<your-render-service>.onrender.com/api/telegram/webhook
```

## Telegram Notification Policy

Automatic Telegram notifications are limited to trade execution events only.
System lifecycle events are still stored in SQLite logs, but they are not pushed
to Telegram.

Allowed automatic native event types:

```text
opened
tp1_closed
tp2_closed
tp3_closed
be_moved
position_closed
closed_by_signal
open_failed
close_failed
error
```

Hidden system events:

```text
ack
ack_received
acknowledged
sent
queued
command_queued
webhook_signal_received
mt5_command_sent
close_signal_received
rejected_signal
account_snapshot
positions_snapshot
heartbeat
command_received
settings_changed
audit_log
execution_report_received without a trade status from the whitelist
```

Telegram commands such as `/status`, `/account`, `/positions`, `/trades`, and
`/news` still return direct replies when requested, but they do not create extra
automatic notifications. Confirmation stays enabled for risk/live actions:
`/dryrun_off`, lot changes, risk setting changes, and `/pause`/`/resume`.

## Safety

- Risk settings change only through `/confirm <approval_id>`.
- Telegram masks account login values.
- Telegram never displays secrets, tokens, passwords, or API keys.
- Market research is informational only and does not open or close trades.
- If AI suggests a risk action, the bot creates pending approval only.
- If MT5 reports account mode as real/live, Telegram shows a REAL account warning.

Demo-first guardrails:

- `dry_run` defaults to `true`.
- Telegram can set `dry_run=false` only through pending approval when
  `ALLOW_REAL_TRADING=true`; otherwise real-account unlock is blocked.
- Lot multipliers cannot exceed `3.0`.
- Unknown symbols are rejected.
- `trading_enabled=false` blocks new open signals on the server, while close
  signals remain accepted.

Allowed control symbols:

```text
XAUUSD, NAS100, DJ30, US500, BTCUSD
```

Symbol aliases:

```text
SP500 = US500
US500 = US500
NAS100 = NAS100
DJ30 = DJ30
XAUUSD = XAUUSD
BTCUSD = BTCUSD
```

## MT5 Account Reporting

The EA periodically posts account and positions snapshots, and posts a deal
report after a position closes.

Account snapshot:

```json
{
  "balance": 10000.0,
  "equity": 10025.5,
  "margin": 250.0,
  "free_margin": 9775.5,
  "margin_level": 4010.2,
  "currency": "USD",
  "account_login": "12345678",
  "account_server": "Broker-Demo",
  "trade_mode": "demo"
}
```

Positions snapshot:

```json
{
  "snapshot_at": "2026-05-02T14:15:00Z",
  "positions": [
    {
      "ticket": 123456,
      "symbol": "NAS100",
      "side": "buy",
      "lot": 0.02,
      "entry_price": 18450.25,
      "current_price": 18472.5,
      "sl": 18400.0,
      "tp": 18520.0,
      "profit": 4.45,
      "swap": 0.0,
      "commission": -0.2,
      "magic": 26043001,
      "comment": "tv-smob",
      "opened_at": "2026-05-02T13:55:00Z"
    }
  ]
}
```

Deal report:

```json
{
  "deal_ticket": 987654,
  "position_ticket": 123456,
  "symbol": "NAS100",
  "side": "buy",
  "lot": 0.02,
  "entry_price": 18450.25,
  "exit_price": 18490.0,
  "profit": 7.95,
  "commission": -0.2,
  "swap": 0.0,
  "net_profit": 7.75,
  "opened_at": "2026-05-02T13:55:00Z",
  "closed_at": "2026-05-02T14:20:00Z",
  "reason": "tp1_closed",
  "magic": 26043001,
  "comment": "tv-smob"
}
```

## Legacy TradingView Command Contract

This contract is active only when `SYSTEM_MODE=TRADINGVIEW_BRIDGE`. In the
default `NATIVE_MT5_ONLY` mode, TradingView webhooks are accepted as disabled
requests and no commands are queued or delivered to MT5.

The TradingView webhook accepts two command types:

- `action=open` creates a queued open command. It requires `entry`, `sl`,
  `tp_count`, TP price/quantity fields, `lot`, `magic_number`, `symbol`, and
  `mt5_symbol`.
- `action=close` creates a queued close command. It requires `signal_id`,
  `parent_signal_id`, `side`, `reason`, `magic_number`, and at least one of
  `mt5_symbol` or `symbol`.

Both command types are deduplicated by `signal_id`, stored in SQLite with
`status=queued`, and delivered to MT5 by `GET /api/mt5/commands`.

## Project Structure

```text
server/
  main.py               FastAPI app and routes
  config.py             Environment variable loading
  models.py             Pydantic request/response models
  validators.py         Signal validation
  database.py           SQLite connection and schema init
  account_store.py      MT5 account, positions, deals, PnL storage
  queue.py              Queue operations
  telegram_bot.py       Telegram dashboard, commands, approvals
  settings_store.py     Bot settings, approvals, audit log
  ai_command_parser.py  OpenAI parser with regex fallback
  ai_web_research.py    Market news and web research
  symbol_mapper.py      TradingView -> MT5 symbol lookup
```
## Trading Control Center

Trading Control Center is a compatibility layer over the existing FastAPI,
Telegram, and native MT5 storage. It does not replace the old native endpoints
or the `native_*` tables.

Telegram main buttons:

```text
Включить бота | Остановить бота
📊 Статистика | 📒 Журнал
📸 Последний скрин | Настройки
Статус | Сделки
```

Bot control uses these assets and bot IDs:

```text
NAS100  -> NAS100_ORB_VWAP_RSI_OF
SP500   -> SP500_ORB_VWAP_RSI_OF
DJ30    -> DJ30_ORB_VWAP_RSI_OF
BTCUSD  -> BTCUSD_ORB_VWAP_RSI_OF
GER40   -> GER40_ORB_VWAP_RSI_OF
```

Telegram commands:

```text
/performance
/performance NAS100
/performance SP500
/performance DJ30
/performance BTCUSD
/performance GER40
/journal
/journal NAS100
/trades_today
/trades_today NAS100
/last_screenshot
/last_screenshot NAS100
/daily_report
/symbols
```

Control endpoints:

```text
GET  /api/mt5/native-config?secret=...&bot_id=...
GET  /api/bots/status
POST /api/bots/enable
POST /api/bots/disable
POST /api/tasks/daily-report
```

`GET /api/mt5/native-config` is intended for MT5 polling. It checks
`MT5_NATIVE_SECRET`, with fallback to `WEBHOOK_SECRET`, and returns the bot
enabled state. If a bot is disabled, the EA should block only new entries and
continue managing already open positions.

`POST /api/bots/enable`, `POST /api/bots/disable`, and
`POST /api/tasks/daily-report` check body `secret` against `TASK_SECRET`, with
fallback to `MT5_NATIVE_SECRET`, then `WEBHOOK_SECRET`.

Example bot disable body:

```json
{
  "secret": "$TASK_SECRET",
  "bot_id": "NAS100_ORB_VWAP_RSI_OF",
  "reason": "manual pause"
}
```

Render Cron:

```text
0 * * * *
```

Cron should call:

```text
POST https://<service>.onrender.com/api/tasks/daily-report
```

Body:

```json
{
  "secret": "$TASK_SECRET"
}
```

The endpoint checks the Europe/Berlin hour and sends the report once per Berlin
day at 21:00. `force=true` bypasses the hour and idempotency checks for manual
use.
