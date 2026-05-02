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
| POST   | `/api/telegram/webhook`       | Telegram bot command webhook             |
| GET    | `/api/settings`               | Current server-side bot settings         |
| POST   | `/api/settings`               | Update a setting with `WEBHOOK_SECRET`   |
| GET    | `/api/audit-log`              | Approval/audit history                   |

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

| Command       | Description                                      |
|---------------|--------------------------------------------------|
| `/status`     | Server status, trading flag, queue counts, last signal, last report |
| `/last_trade` | Latest execution report                          |
| `/today`      | Today's signal, opened, rejected, and PnL summary |
| `/news`       | Today's market news for USD, indices, gold, crypto, oil if relevant |
| `/calendar`   | Today's high-impact economic calendar in Europe/Berlin time |
| `/market_today` | Short trading risk overview for today          |
| `/ask <question>` | Ask the AI research assistant; uses web search for fresh data |
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

## Project Structure

```
tv-mt5-bridge/
├── server/
│   ├── main.py          # FastAPI app and routes
│   ├── config.py        # Environment variable loading
│   ├── models.py        # Pydantic request/response models
│   ├── validators.py    # Signal business logic validation
│   ├── database.py      # SQLite connection and schema init
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
