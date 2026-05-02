# TradingView -> MT5 Bridge

FastAPI bridge for TradingView webhooks, MT5 polling, Telegram control, account
reporting, and AI market research.

## Architecture

```text
TradingView alert
      | POST /api/webhook/tradingview
      v
  FastAPI server -> SQLite queue
      ^
      | GET /api/mt5/commands
      | POST /api/mt5/ack
      | POST /api/mt5/execution-report
  MT5 EA / script
```

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
DB_FILE=bridge.db
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_CHAT_ID=your-telegram-admin-chat-id
TRADING_ENABLED=true
OPENAI_API_KEY=optional-openai-key
OPENAI_MODEL=gpt-5.5
OPENAI_TIMEOUT_SECONDS=60
ENABLE_AI_WEB_SEARCH=true
```

Do not commit real Telegram tokens, webhook secrets, MT5 passwords, or OpenAI
API keys.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/webhook/tradingview` | Receive TradingView signal |
| GET | `/api/mt5/commands` | MT5 polls next queued command |
| POST | `/api/mt5/ack` | MT5 acknowledges command receipt |
| POST | `/api/mt5/execution-report` | MT5 reports execution status |
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
| `/dryrun_off` | Заблокировано demo-first режимом |
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

## Safety

- Risk settings change only through `/confirm <approval_id>`.
- Telegram masks account login values.
- Telegram never displays secrets, tokens, passwords, or API keys.
- Market research is informational only and does not open or close trades.
- If AI suggests a risk action, the bot creates pending approval only.
- If MT5 reports account mode as real/live, Telegram shows a REAL account warning.

Demo-first guardrails:

- `dry_run` defaults to `true`.
- Telegram cannot set `dry_run=false`.
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

## Command Contract

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
