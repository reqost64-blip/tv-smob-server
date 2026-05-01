# MT5 Execution Rules

## Command Lifecycle

```
queued → sent → acknowledged → (execution-report received)
```

| Status         | Meaning                                              |
|----------------|------------------------------------------------------|
| `queued`       | Signal validated and stored, waiting for MT5 to poll |
| `sent`         | MT5 fetched the command via GET /api/mt5/commands    |
| `acknowledged` | MT5 confirmed receipt via POST /api/mt5/ack         |

## Polling Protocol (MT5 side)

1. MT5 polls `GET /api/mt5/commands` on a fixed interval (e.g. every 1s).
2. Server returns the oldest `queued` command and immediately marks it `sent`.
3. MT5 confirms receipt with `POST /api/mt5/ack` → status becomes `acknowledged`.
4. MT5 executes the trade and sends the result via `POST /api/mt5/execution-report`.

### GET /api/mt5/commands

Response when a command is available:

```json
{
  "ok": true,
  "command": {
    "signal_id": "tv-20240101-001",
    "status": "sent",
    "payload": { ... full signal payload ... }
  }
}
```

Response when queue is empty:

```json
{
  "ok": true,
  "command": null
}
```

### POST /api/mt5/ack

```json
{ "signal_id": "tv-20240101-001" }
```

### POST /api/mt5/execution-report

```json
{
  "signal_id": "tv-20240101-001",
  "ticket": 123456789,
  "status": "filled",
  "message": "Order executed successfully",
  "executed_price": 45012.5,
  "executed_at": "2024-01-01T12:00:05Z"
}
```

`status` field can be: `filled`, `rejected`, `error`, or any custom string.

## Important Notes

- Only one command is returned per poll (FIFO order).
- Commands are NOT re-queued automatically if MT5 crashes after `sent`.
- Implement a timeout/retry mechanism on the MT5 side if needed.
- `magic_number` in the payload identifies which EA placed the order.
