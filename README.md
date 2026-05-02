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

## Telegram Bot Control Layer

Telegram support is read-only in this stage. The server sends notifications for
incoming TradingView signals, queued commands, MT5 command delivery,
acknowledgements, execution reports, rejected signals, and execution statuses such
as `open_failed`, `opened`, `tp1_closed`, `tp2_closed`, `tp3_closed`, `be_moved`,
and `position_closed`.

Required Render environment variables:

```
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-telegram-admin-chat-id
```

Do not commit real Telegram tokens to GitHub.

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
| `/help`       | Command list                                     |

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
│   ├── telegram_bot.py  # Telegram notifications and read-only commands
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
