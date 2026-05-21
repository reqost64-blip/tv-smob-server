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
        "MT5 Command",
        "Broker Dashboard",
        "#f6f7fb",
        "#ffffff",
        "Главная",
        "Портфель",
        "Позиции",
        "Сделки",
        "История",
        "Аналитика",
        "Риск",
        "Bias",
        "Strategy Lab",
        "Storage",
        "Настройки",
        "/api/dashboard/storage-health",
        "/api/dashboard/system",
        "/api/dashboard/account-history",
        "/api/dashboard/bias/live",
        "/api/dashboard/bias/live/history",
        "Live Bias",
        "Pre-Market Bias",
        "Live Factor Breakdown",
        "Not enough equity history yet.",
        "No closed trade history found. Import MT5 history.",
        "bottom-nav",
        "more-sheet",
        "MOBILE_NAV",
        "MORE_ROUTES",
        "max-width: 767px",
        "min-width: 768px",
        "max-width: 1023px",
        "min-width: 1024px",
        "mobile-cards",
        "compact-card",
        "tradeCompactCard",
        "data-label",
        "endpoint-error",
        "return fallback",
    ]
    missing = [item for item in required if item not in html]
    assert not missing, missing
    forbidden = ["black-hole", "particle-canvas", "Orbit Command", "radial-gradient(circle at"]
    present = [item for item in forbidden if item in html]
    assert not present, present
    for route in ["overview", "portfolio", "positions", "trades", "history", "analytics", "risk", "bias", "lab", "storage", "settings"]:
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
