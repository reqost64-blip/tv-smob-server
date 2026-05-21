import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.bias_sources import SeriesBundle
from server.live_bias_engine import calculate_live_symbol_bias, live_probabilities


def bundle(start=100.0, step=1.0, count=80):
    closes = [start + step * i for i in range(count)]
    highs = [value + 0.8 for value in closes]
    lows = [value - 0.8 for value in closes]
    volumes = [1000 + i for i in range(count)]
    return SeriesBundle(closes=closes, highs=highs, lows=lows, volumes=volumes, source="synthetic")


def context(series, macro=None):
    return {
        "timeframes": {"NAS100": {"D1": series, "H4": series, "H1": series, "M15": series, "M5": series}},
        "market_data": {},
        "source_availability": {
            "market_data": "available",
            "intermarket": "unavailable",
            "macro_calendar": "available",
            "news_sentiment": "available",
            "binance_derivatives": "unavailable",
        },
        "macro": macro or {"availability": "available", "risk": "LOW", "score": 0, "confidence": 70, "reasons": []},
        "news": {"availability": "available", "score": 0, "confidence": 55, "reasons": []},
        "binance": {"availability": "unavailable", "score": 0, "confidence": 0, "reasons": []},
    }


def main():
    item = {"symbol": "NAS100", "bot_id": "test", "ticker": "NQ=F"}
    positive = calculate_live_symbol_bias(item, context(bundle(step=1.0)))
    assert positive["direction"] == "LONG", positive
    assert positive["confidence"] >= 51, positive
    negative = calculate_live_symbol_bias(item, context(bundle(start=200, step=-1.0)))
    assert negative["direction"] == "SHORT", negative
    weak_conf, long_pct, short_pct = live_probabilities(2, "LONG", 80, 0.5, 0.5, "NORMAL", "LOW", {})
    assert weak_conf in range(51, 57), weak_conf
    assert long_pct > short_pct
    low_quality = calculate_live_symbol_bias(item, {"timeframes": {}, "market_data": {}, "source_availability": {}, "macro": {}, "news": {}, "binance": {}})
    assert low_quality["direction"] in {"LONG", "SHORT"}
    assert low_quality["confidence"] <= 56, low_quality
    assert "LOW_DATA_QUALITY" in low_quality["risk_flags"], low_quality
    high_macro = calculate_live_symbol_bias(
        item,
        context(bundle(step=1.0), macro={"availability": "available", "risk": "HIGH", "score": 0, "confidence": 70, "reasons": ["CPI"]}),
    )
    assert "HIGH_MACRO_RISK" in high_macro["risk_flags"], high_macro
    assert high_macro["risk"] == "HIGH", high_macro
    assert high_macro["direction"] in {"LONG", "SHORT"}
    print({"live_bias_direction": "ok"})


if __name__ == "__main__":
    main()
