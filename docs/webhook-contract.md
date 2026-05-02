# Webhook Contract - TradingView to Bridge

## Endpoint

```
POST /api/webhook/tradingview
Content-Type: application/json
```

## Request Body

| Field                  | Type    | Required | Description                                          |
|------------------------|---------|----------|------------------------------------------------------|
| `version`              | string  | no       | Payload contract version                             |
| `secret`               | string  | yes      | Must match `WEBHOOK_SECRET` env var                  |
| `source`               | string  | no       | Signal source, usually `tradingview`                 |
| `signal_id`            | string  | yes      | Unique ID for deduplication                          |
| `symbol`               | string  | open     | TradingView ticker (e.g. `BTCUSDT`)                  |
| `mt5_symbol`           | string  | open     | MT5 symbol name (e.g. `BTCUSDm`)                     |
| `timeframe`            | string  | yes      | Chart timeframe (e.g. `1h`)                          |
| `time`                 | string  | yes      | Signal timestamp (ISO 8601 or Unix string)           |
| `action`               | string  | yes      | `open` or `close`                                    |
| `side`                 | string  | yes      | `buy` or `sell`                                      |
| `parent_signal_id`     | string  | close    | Original open signal ID to close                     |
| `reason`               | string  | close    | Close reason from the TradingView strategy           |
| `close_price`          | float   | no       | TradingView close price                              |
| `entry`                | float   | open     | Entry price                                          |
| `sl`                   | float   | open     | Stop-loss price                                      |
| `tp_count`             | int     | open     | Number of take-profits: `1`, `2`, or `3`             |
| `tp1`                  | float   | cond.    | Required when `tp_count >= 1`                        |
| `tp1_qty`              | float   | cond.    | Percentage of position for TP1. Sum must be about 100 |
| `tp2`                  | float   | cond.    | Required when `tp_count >= 2`                        |
| `tp2_qty`              | float   | cond.    | Percentage of position for TP2                       |
| `tp3`                  | float   | cond.    | Required when `tp_count >= 3`                        |
| `tp3_qty`              | float   | cond.    | Percentage of position for TP3                       |
| `move_to_be_after_first_tp` | bool | no  | Move SL to break-even after first TP hit             |
| `be_trigger_tp_id`     | int     | no       | Which TP triggers BE move (1, 2, or 3)               |
| `lot`                  | float   | open     | Position size in lots (> 0)                          |
| `magic_number`         | int     | yes      | MT5 magic number for order identification            |

## Validation Rules

- `action` must be `open` or `close`
- `side` must be `buy` or `sell`
- `signal_id` must be globally unique

### Open Commands

- `symbol`, `mt5_symbol`, `entry`, `sl`, `tp_count`, `lot`, and `magic_number` are required
- `tp_count` must be 1, 2, or 3
- Sum of all `tp_qty` fields must equal 100 +/- 0.2
- For `buy`: `sl < entry`, all `tp > entry`
- For `sell`: `sl > entry`, all `tp < entry`

### Close Commands

- `secret`, `signal_id`, `parent_signal_id`, `side`, `reason`, and `magic_number` are required
- At least one of `mt5_symbol` or `symbol` is required
- `entry`, `sl`, `tp_count`, `tp1`, `tp2`, `tp3`, and TP quantity fields are not required
- Close commands are stored in the same SQLite queue with `status=queued`
- MT5 receives close commands from `GET /api/mt5/commands` with the original payload under `command.payload`

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

## Example Open Payload

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

## Example Close Payload

```json
{
  "version": "1.0",
  "secret": "your-secret-here",
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
