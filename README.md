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
