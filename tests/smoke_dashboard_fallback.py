from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    html = DASHBOARD.read_text(encoding="utf-8")
    required = [
        "async function api",
        "state.errors[key]",
        "return fallback",
        "endpoint-error",
        "errorBlock(endpointKey)",
        "card('Equity chart', lineChart(equityPoints()), '', 'accountHistory')",
        "card('Open Position', positionsPanel(1), '', 'positions')",
        "card('Trades', tradeTable(rows), '', 'trades')",
        "Promise.all",
        "cache:'no-store'",
    ]
    missing = [item for item in required if item not in html]
    assert not missing, missing
    print({"dashboard_fallback": "ok"})


if __name__ == "__main__":
    main()
