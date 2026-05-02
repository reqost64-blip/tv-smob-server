# TradingView → MT5 Bridge

Receives webhook signals from TradingView, validates them, stores them in an SQLite queue,
and serves them to an MT5 Expert Advisor on demand.

## Architecture

```
TradingView alert
      │ POST /api/webhook/tradingview
      ▼
  [FastAPI server]  ──► SQLite (status: queued → sent → acknowledged)
      ▲
      │ GET /api/mt5/commands (poll)
      │ POST /api/mt5/ack
      │ POST /api/mt5/execution-report
  [MT5 EA / script]
```

## Quick Start

### 1. Clone and set up the environment

```bash
cd C:\Projects\tv-mt5-bridge\server
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set a strong secret:

```
WEBHOOK_SECRET=change-this-to-a-random-secret
DB_FILE=bridge.db
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-telegram-admin-chat-id
TRADING_ENABLED=true
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5
OPENAI_TIMEOUT_SECONDS=60
ENABLE_AI_WEB_SEARCH=true
```

### 3. Run the server

```bash
# From the project root (C:\Projects\tv-mt5-bridge)
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

The server starts at `http://localhost:8000`.

### 4. Verify

```
GET http://localhost:8000/api/health
→ {"ok": true, "status": "running"}
```

Interactive API docs: `http://localhost:8000/docs`

## Endpoints

| Method | Path                          | Description                              |
|--------|-------------------------------|------------------------------------------|
| GET    | `/api/health`                 | Health check                             |
| POST   | `/api/webhook/tradingview`    | Receive signal from TradingView          |
| GET    | `/api/mt5/commands`           | MT5 polls for next queued command        |
| POST   | `/api/mt5/ack`                | MT5 acknowledges command receipt         |
| POST   | `/api/mt5/execution-report`   | MT5 reports order execution result       |
| POST   | `/api/mt5/account-snapshot`   | MT5 posts account balance/equity snapshot |
| POST   | `/api/mt5/positions-snapshot` | MT5 posts current open positions snapshot |
| POST   | `/api/mt5/deal-report`        | MT5 posts a closed deal report           |
| POST   | `/api/telegram/webhook`       | Telegram bot command webhook             |
| GET    | `/api/settings`               | Current server-side bot settings         |
| POST   | `/api/settings`               | Update a setting with `WEBHOOK_SECRET`   |
| GET    | `/api/audit-log`              | Approval/audit history                   |
| GET    | `/api/account`                | Latest account snapshot                  |
| GET    | `/api/positions`              | Latest open positions snapshot           |
| GET    | `/api/trades/today`           | Today's deal reports                     |
| GET    | `/api/pnl/today`              | Today's PnL summary                      |

## Command Contract

The TradingView webhook accepts two command types:

- `action=open` creates a new queued open command. It requires `entry`, `sl`,
  `tp_count`, the matching TP price/quantity fields, `lot`, `magic_number`,
  `symbol`, and `mt5_symbol`.
- `action=close` creates a new queued close command. It requires `signal_id`,
  `parent_signal_id`, `side`, `reason`, `magic_number`, and at least one of
  `mt5_symbol` or `symbol`. It does not require `entry`, `sl`, `tp_count`, or TP
  fields.

Both command types are deduplicated by `signal_id`, stored in SQLite with
`status=queued`, and delivered to MT5 by `GET /api/mt5/commands`.

Example close payload:

```json
{
  "version": "1.0",
  "secret": "change-this-to-a-random-secret",
  "source": "tradingview",
  "signal_id": "tv-20260501-close-001",
  "parent_signal_id": "tv-20260501-open-001",
  "symbol": "SP500",
  "mt5_symbol": "US500",
  "timeframe": "3",
  "time": "2026-05-01T20:55:00Z",
  "action": "close",
  "side": "sell",
  "reason": "return_inside_orb",
  "close_price": 7239.11,
  "magic_number": 26043001
}
```

## Telegram AI Control Layer

Telegram support sends notifications for incoming TradingView signals, queued
commands, MT5 command delivery, acknowledgements, execution reports, rejected
signals, and execution statuses such as `open_failed`, `opened`, `tp1_closed`,
`tp2_closed`, `tp3_closed`, `be_moved`, and `position_closed`.

It also accepts safe natural-language control commands in Russian. Any setting
change creates a pending approval first. The server applies the change only after
`/confirm <approval_id>`. Rejections and applied changes are written to
`audit_log`.

Demo-first guardrails:

- `dry_run` defaults to `true`.
- Telegram cannot set `dry_run=false`.
- Lot multipliers cannot exceed `3.0`.
- Unknown symbols are rejected.
- `trading_enabled=false` blocks new open signals on the server, while close
  signals remain accepted.

Allowed control symbols:

```
XAUUSD, NAS100, DJ30, US500, BTCUSD
```

Symbol aliases:

```
SP500 = US500
US500 = US500
NAS100 = NAS100
DJ30 = DJ30
XAUUSD = XAUUSD
BTCUSD = BTCUSD
```

Symbol-specific lot settings:

```
symbol_lot_multiplier_XAUUSD
symbol_lot_multiplier_NAS100
symbol_lot_multiplier_DJ30
symbol_lot_multiplier_US500
symbol_lot_multiplier_BTCUSD
```

Required Render environment variables:

```
WEBHOOK_SECRET=your-secret-here
DB_FILE=bridge.db
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-telegram-admin-chat-id
TRADING_ENABLED=true
OPENAI_API_KEY=optional-openai-key
OPENAI_MODEL=gpt-5.5
OPENAI_TIMEOUT_SECONDS=60
ENABLE_AI_WEB_SEARCH=true
```

`OPENAI_API_KEY` is optional. If it is missing or parsing fails, the server uses
a regex fallback parser for the supported Russian phrases. Do not commit real
Telegram tokens, webhook secrets, or OpenAI keys to GitHub.

`ENABLE_AI_WEB_SEARCH=true` enables OpenAI Responses API web search for market
news and fresh-data questions. Market research answers are informational only:
the bot does not open trades, close trades, or change risk based on news. If an
AI answer contains a risk action suggestion such as pausing a symbol, the server
creates a pending approval and applies it only after `/confirm <approval_id>`.

Set the Telegram webhook to:

```
https://<your-render-service>.onrender.com/api/telegram/webhook
```

Supported Telegram commands:

The bot sends a reply keyboard with four dashboard buttons:

| Button | Action |
|--------|--------|
| `📊 Core Status` | `/status` |
| `📈 Trade Center` | `/trades` |
| `📰 Market Intel` | `/market_today` |
| `⚙️ Control Panel` | `/settings` |

Legacy buttons (`Статус`, `Сделки`, `Новости`, `⚙️ Управление`) remain supported.

> **Note:** All message formatting uses plain text + emojis. No Markdown or HTML
> is used, so messages render correctly on all Telegram clients and mobile devices.
> No trading logic, risk controls, Pine Script, or MQL5 EA were modified.

| Command       | Description                                      |
|---------------|--------------------------------------------------|
| `/status`     | ⚡ SYSTEM CORE — server, MT5 link, trading flag, account, queue |
| `/last_trade` | Latest execution report                          |
| `/today`      | Today's signal, opened, rejected, and PnL summary |
| `/account`    | 💰 ACCOUNT MATRIX — login, mode, balance, equity, margin |
| `/balance`    | Account balance                                  |
| `/equity`     | Account equity                                   |
| `/positions`  | 📈 OPEN POSITIONS or 📭 NO OPEN POSITIONS        |
| `/trades`     | 🏁 TODAY TRADES or 📭 NO TRADES TODAY            |
| `/history_today` | 📊 DAILY PERFORMANCE — wins, losses, winrate, PnL |
| `/pnl_today`  | Today's PnL summary                              |
| `/news`       | 📰 MARKET INTEL — market news via AI web search  |
| `/calendar`   | 📅 ECONOMIC CALENDAR — high-impact events today  |
| `/market_today` | 📰 MARKET INTEL — trading risk overview today  |
| `/ask <question>` | AI research assistant with web search        |
| `/settings`   | Current bot settings                             |
| `/risk`       | Current risk controls                            |
| `/approvals`  | Pending approvals                                |
| `/confirm <approval_id>` | Apply a pending approval              |
| `/reject <approval_id>`  | Reject a pending approval              |
| `/pause`      | Create approval to disable new open signals      |
| `/resume`     | Create approval to enable new open signals       |
| `/dryrun_on`  | Create approval to enable dry run                |
| `/dryrun_off` | Blocked in demo-first mode                       |
| `/help`       | Command list                                     |

Example message formats:

```
⚡ SYSTEM CORE

🟢 Server: ONLINE
🟢 MT5 Link: ACTIVE
🟢 Trading: ENABLED
🟡 DryRun: ON

💰 ACCOUNT
Balance: 10000.00 USD
Equity: 10025.50 USD
Today PnL: 25.50 USD

📡 EXECUTION
Open positions: 1
Queued commands: 0
Last MT5 heartbeat: 2026-05-02T14:15:00Z
```

```
💰 ACCOUNT MATRIX

Login: ****5678
Server: Broker-Demo
Mode: DEMO
Balance: 10000.00 USD
Equity: 10025.50 USD
Margin: 250.00 USD
Free margin: 9775.50 USD
Margin level: 4010.20%
```

```
📈 OPEN POSITIONS

1. NAS100 BUY
Lot: 0.02
Entry: 18450.25
Current: 18472.5
SL: 18400
TP: 18520
Floating PnL: 4.45
Ticket: 123456
```

```
🏁 TODAY TRADES

1. NAS100 BUY
Entry: 18450.25
Exit: 18490
Lot: 0.02
Net PnL: 7.75
Reason: tp1_closed
```

```
📊 DAILY PERFORMANCE

Trades: 3
Wins: 2
Losses: 1
Winrate: 67%
Net PnL: 15.30
Best trade: 12.50
Worst trade: -4.20
```

Execution notifications:

```
🚀 TRADE OPENED
Asset: NAS100
Side: BUY
Lot: 0.02
Entry: 18450.25
SL: 18400
TP1: 18490
TP2: 18520
Signal: tv-20260502-001
```

```
🏁 TRADE CLOSED
Asset: NAS100
Side: BUY
Lot: 0.02
Entry: 18450.25
Exit: 18490
Reason: TP1 hit
Ticket: 123456
```

```
🚨 EXECUTION ERROR
Signal: tv-20260502-001
Asset: NAS100
Error: Margin not sufficient
Action required: check MT5 manually
```

Supported Russian natural-language examples:

```
поставь лот 0.02 на nas100
повысь лот на 20 процентов на nas100
уменьши лот на 30 процентов на btcusd
останови торговлю
включи торговлю
включи dry run
выключи dry run
покажи настройки
какой риск сейчас
покажи последние сделки
какие новости сегодня
что сегодня важно по рынку
что влияет на золото сегодня
почему nas100 падает
останови торговлю по NAS100 на 30 минут
```

Example approval response:

```
Команда распознана:
Параметр: symbol_lot_multiplier_NAS100
Старое значение: 1.0
Новое значение: 1.2
Approval ID: abc123def0

Для применения напиши:
/confirm abc123def0
```

Security notes:

- Telegram masks account login values.
- Do not send or store MT5 passwords.
- Telegram never displays secrets or API keys.
- If MT5 reports account mode as real/live, Telegram shows a warning.

## MT5 Account Reporting

The EA should periodically post account and positions snapshots, and post a deal
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

## Project Structure

```
tv-mt5-bridge/
├── server/
│   ├── main.py          # FastAPI app and routes
│   ├── config.py        # Environment variable loading
│   ├── models.py        # Pydantic request/response models
│   ├── validators.py    # Signal business logic validation
│   ├── database.py      # SQLite connection and schema init
│   ├── account_store.py # MT5 account, positions, deals, PnL storage
│   ├── queue.py         # Queue operations (enqueue, fetch, ack)
│   ├── telegram_bot.py  # Telegram notifications, commands, approvals
│   ├── settings_store.py # Bot settings, pending approvals, audit log
│   ├── ai_command_parser.py # OpenAI parser with regex fallback
│   ├── ai_web_research.py # Responses API market news and web research
│   ├── symbol_mapper.py # TV → MT5 symbol lookup
│   ├── requirements.txt
│   └── .env.example
├── config/
│   └── symbols.json     # Symbol name mapping table
└── docs/
    ├── webhook-contract.md
    ├── mt5-execution-rules.md
    └── symbol-mapping.md
```

## Sending a Test Webhook

```bash
curl -X POST http://localhost:8000/api/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "change-this-to-a-random-secret",
    "signal_id": "test-001",
    "symbol": "BTCUSDT",
    "mt5_symbol": "BTCUSDm",
    "timeframe": "1h",
    "time": "2024-01-01T12:00:00Z",
    "action": "open",
    "side": "buy",
    "entry": 45000,
    "sl": 44000,
    "tp_count": 2,
    "tp1": 46000,
    "tp1_qty": 60,
    "tp2": 47000,
    "tp2_qty": 40,
    "move_to_be_after_first_tp": true,
    "be_trigger_tp_id": 1,
    "lot": 0.1,
    "magic_number": 12345
  }'
```
