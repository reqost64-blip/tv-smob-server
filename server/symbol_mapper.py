import json
import os
from typing import Optional

_SYMBOLS_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "symbols.json")
_mapping: dict[str, str] = {}


def load_symbols() -> None:
    global _mapping
    try:
        with open(_SYMBOLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _mapping = data.get("tv_to_mt5", {})
    except FileNotFoundError:
        _mapping = {}


def tv_to_mt5(tv_symbol: str) -> Optional[str]:
    return _mapping.get(tv_symbol)
