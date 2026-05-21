from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "Сигналы",
        "/api/dashboard/signals",
        "/api/dashboard/signals/sources",
        "/api/dashboard/signals/accuracy",
        "Страница в разработке · Данные скоро появятся",
        "Байес · Live",
        "Последние сделки",
    ]
    missing = [item for item in required if item not in HTML]
    assert not missing, missing
    print({"signal_dashboard": "ok"})


if __name__ == "__main__":
    main()
