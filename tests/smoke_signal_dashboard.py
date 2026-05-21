from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "renderSignals",
        "renderSources",
        "signalRows",
        "signalSources",
        "signalAccuracy",
        "sourceTrust",
        "/api/dashboard/signals",
        "/api/dashboard/signals/sources",
        "/api/dashboard/signals/accuracy",
        "No signals processed yet.",
        "VALID_SIGNAL",
        "WAIT_CONFIRMATION",
        "DUPLICATE",
        "mobile-cards",
    ]
    missing = [item for item in required if item not in HTML]
    assert not missing, missing
    assert "skeletonPage" not in HTML
    print({"signal_dashboard": "ok"})


if __name__ == "__main__":
    main()
