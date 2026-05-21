from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "Signals",
        "/api/dashboard/signals",
        "/api/dashboard/signals/sources",
        "/api/dashboard/signals/accuracy",
        "Latest Signals",
        "Signal Sources",
        "Signal Accuracy",
        "No signal sources connected yet.",
        "No validated signals yet.",
        "No evaluated signals yet.",
    ]
    missing = [item for item in required if item not in HTML]
    assert not missing, missing
    print({"signal_dashboard": "ok"})


if __name__ == "__main__":
    main()
