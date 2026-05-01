# Webhook Contract — TradingView → Bridge

## Endpoint

```
POST /api/webhook/tradingview
Content-Type: application/json
```

## Request Body

| Field                  | Type    | Required | Description                                          |
|------------------------|---------|----------|------------------------------------------------------|
| `secret`               | string  | yes      | Must match `WEBHOOK_SECRET` env var                  |
| `signal_id`            | string  | yes      | Unique ID for deduplication                          |
| `symbol`               | string  | yes      | TradingView ticker (e.g. `BTCUSDT`)                  |
| `mt5_symbol`           | string  | yes      | MT5 symbol name (e.g. `BTCUSDm`)                    |
| `timeframe`            | string  | yes      | Chart timeframe (e.g. `1h`)                          |
| `time`                 | string  | yes      | Signal timestamp (ISO 8601 or Unix string)           |
| `action`               | string  | yes      | Must be `open`                                       |
| `side`                 | string  | yes      | `buy` or `sell`                                      |
| `entry`                | float   | yes      | Entry price                                          |
| `sl`                   | float   | yes      | Stop-loss price                                      |
| `tp_count`             | int     | yes      | Number of take-profits: `1`, `2`, or `3`             |
| `tp1`                  | float   | cond.    | Required when `tp_count >= 1`                        |
| `tp1_qty`              | float   | cond.    | Percentage of position for TP1. Sum must ≈ 100       |
| `tp2`                  | float   | cond.    | Required when `tp_count >= 2`                        |
| `tp2_qty`              | float   | cond.    | Percentage of position for TP2                       |
| `tp3`                  | float   | cond.    | Required when `tp_count >= 3`                        |
| `tp3_qty`              | float   | cond.    | Percentage of position for TP3                       |
| `move_to_be_after_first_tp` | bool | no  | Move SL to break-even after first TP hit            |
| `be_trigger_tp_id`     | int     | no       | Which TP triggers BE move (1, 2, or 3)               |
| `lot`                  | float   | yes      | Position size in lots (> 0)                          |
| `magic_number`         | int     | yes      | MT5 magic number for order identification            |

## Validation Rules

- `action` must be `open`
- `side` must be `buy` or `sell`
- `tp_count` must be 1, 2, or 3
- Sum of all `tp_qty` fields must equal 100 ± 0.2
- For **buy**: `sl < entry`, all `tp > entry`
- For **sell**: `sl > entry`, all `tp < entry`
- `signal_id` must be globally unique (deduplication)

## Success Response

```json
{
  "ok": true,
  "signal_id": "abc-123",
  "status": "queued"
}
```

## Error Response

```json
{
  "ok": false,
  "error": "Description of what went wrong"
}
```

## Example Payload

```json
{
  "secret": "your-secret-here",
  "signal_id": "tv-20240101-001",
  "symbol": "BTCUSDT",
  "mt5_symbol": "BTCUSDm",
  "timeframe": "1h",
  "time": "2024-01-01T12:00:00Z",
  "action": "open",
  "side": "buy",
  "entry": 45000.0,
  "sl": 44000.0,
  "tp_count": 3,
  "tp1": 46000.0,
  "tp1_qty": 40.0,
  "tp2": 47000.0,
  "tp2_qty": 30.0,
  "tp3": 49000.0,
  "tp3_qty": 30.0,
  "move_to_be_after_first_tp": true,
  "be_trigger_tp_id": 1,
  "lot": 0.1,
  "magic_number": 12345
}
```
