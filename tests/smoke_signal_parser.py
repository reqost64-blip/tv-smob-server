import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.signal_parser import parse_manual_signal, parse_tradingview_signal


def main():
    tv = parse_tradingview_signal(
        {
            "source": "tradingview",
            "symbol": "US500",
            "direction": "BUY",
            "setup": "VWAP_RECLAIM",
            "entry": 18842.5,
            "sl": 18828.0,
            "tp1": 18862.0,
            "timeframe": "5m",
        }
    )
    assert tv["symbol"] == "SP500", tv
    assert tv["direction"] == "LONG", tv
    assert tv["parse_status"] == "parsed", tv

    manual = parse_manual_signal("TG_Channel_A", "NAS100 LONG entry 18842-18848 SL 18828 TP1 18862 TP2 18884 setup: VWAP reclaim M5")
    assert manual["symbol"] == "NAS100", manual
    assert manual["direction"] == "LONG", manual
    assert manual["entry_zone_low"] == 18842.0, manual
    assert manual["tp2"] == 18884.0, manual
    assert manual["parse_status"] == "parsed", manual

    bad = parse_manual_signal("bad", "hello market maybe up")
    assert bad["parse_status"] == "unparsed", bad
    print({"signal_parser": "ok"})


if __name__ == "__main__":
    main()
