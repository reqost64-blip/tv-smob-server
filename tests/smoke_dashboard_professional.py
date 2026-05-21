import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("WEBHOOK_SECRET", "smoke-test-secret")
os.environ.setdefault("MT5_NATIVE_SECRET", "do-not-leak-smoke-secret")
os.environ.setdefault("DB_FILE", str(Path(__file__).with_name("dashboard_professional.sqlite3")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    html = DASHBOARD.read_text(encoding="utf-8")
    required = [
        "SMOB Торговая панель",
        "Inter:wght",
        "JetBrains+Mono",
        "--page: #F0F2F7",
        "--amber: #D97706",
        "width: 220px",
        "height: 100vh",
        "display: flex",
        "Главная",
        "Счёт",
        "Позиции",
        "Сделки",
        "История",
        "Сигналы",
        "Байес",
        "Статистика",
        "Риск",
        "Strategy Lab",
        "Источники",
        "Хранилище",
        "Render Live",
        "БАЛАНС",
        "Последние сделки",
        "Байес · Live",
        "Аналитика · Риск",
        "Страница в разработке · Данные скоро появятся",
        "bottom-nav",
        "Мобильная навигация",
        "setInterval(refreshAll, REFRESH_MS)",
        "return fallback",
        "/api/dashboard/bias/live",
        "/api/dashboard/signals",
        "/api/dashboard/storage-health",
    ]
    missing = [item for item in required if item not in html]
    assert not missing, missing
    forbidden = ["Orbit Command", "black-hole", "particle-canvas", "Dashboard refresh", "No open positions"]
    present = [item for item in forbidden if item in html]
    assert not present, present
    for route in ["overview", "portfolio", "positions", "trades", "history", "signals", "bias", "analytics", "risk", "lab", "sources", "storage"]:
        assert f"id:'{route}'" in html, route
    subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('fs'); const html=fs.readFileSync('dashboard/index.html','utf8'); const scripts=[...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]).join('\\n'); new Function(scripts);",
        ],
        check=True,
        cwd=ROOT,
    )
    print({"dashboard_professional": "ok", "bytes": len(html)})


if __name__ == "__main__":
    main()
