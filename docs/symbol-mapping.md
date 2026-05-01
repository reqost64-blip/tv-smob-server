# Symbol Mapping

TradingView ticker names often differ from MT5 broker symbol names.
The bridge uses `config/symbols.json` to resolve them automatically.

## How It Works

The webhook payload contains both fields:
- `symbol` — TradingView ticker (informational)
- `mt5_symbol` — exact MT5 symbol name (used for order placement)

The `mt5_symbol` is sent explicitly by the TradingView alert, so the sender
is responsible for setting the correct MT5 name. The `symbol_mapper` module
provides an optional lookup for server-side validation or auto-mapping.

## config/symbols.json Format

```json
{
  "tv_to_mt5": {
    "BTCUSDT": "BTCUSDm",
    "ETHUSDT": "ETHUSDm",
    "EURUSD":  "EURUSD",
    "GBPUSD":  "GBPUSD",
    "XAUUSD":  "XAUUSD"
  }
}
```

## Broker-Specific Notes

- Many brokers append suffixes: `.pro`, `m`, `_SB`, etc.
- Crypto pairs vary: `BTCUSD`, `BTCUSDm`, `BTC/USD`.
- Always verify the exact symbol name in your MT5 Market Watch.
- Update `config/symbols.json` to match your broker's naming.
