from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "function renderSignals",
        "function renderSources",
        "signalRows()",
        "signalSources()",
        "signalAccuracy()",
        "sourceTrust",
        "VALID_SIGNAL",
        "WATCH_ONLY",
        "WAIT_CONFIRMATION",
        "REJECTED",
        "DUPLICATE",
        "EXPIRED",
        "No signals processed yet.",
        "Latest signals",
        "source_name",
        "trust_score",
        "total_signals",
        "average_R",
        "/api/dashboard/signals",
        "/api/dashboard/signals/sources",
        "/api/dashboard/signals/accuracy",
        "desktop-table",
        "mobile-cards",
    ]
    missing = [marker for marker in required if marker not in HTML]
    assert not missing, missing
    print({"dashboard_signals_page": "ok"})


if __name__ == "__main__":
    main()
