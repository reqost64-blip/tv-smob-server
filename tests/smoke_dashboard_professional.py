import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    html = DASHBOARD.read_text(encoding="utf-8")
    required = [
        "SMOB",
        "Inter:wght",
        "JetBrains+Mono",
        "--page: #F0F2F7",
        "--amber: #D97706",
        "width: 220px",
        "height: 100vh",
        "bottom-nav",
        "DASHBOARD_VERSION",
        "REFRESH_MS = 30000",
        "setInterval(refreshAll, REFRESH_MS)",
        "refresh-button",
        "renderOverview",
        "renderPortfolio",
        "renderPositions",
        "renderTrades",
        "renderHistory",
        "renderAnalytics",
        "renderRisk",
        "renderBias",
        "renderLab",
        "renderStorage",
        "renderSignals",
        "renderSources",
        "renderSettings",
        "/api/dashboard/bias/live",
        "/api/dashboard/signals",
        "/api/dashboard/signals/sources",
        "/api/dashboard/signals/accuracy",
        "/api/dashboard/storage-health",
        "/api/dashboard/system",
    ]
    missing = [item for item in required if item not in html]
    assert not missing, missing

    forbidden = [
        "skeletonPage",
        "\u0421\u0442\u0440\u0430\u043d\u0438\u0446\u0430 \u0432 \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0435",
        "\u0414\u0430\u043d\u043d\u044b\u0435 \u0441\u043a\u043e\u0440\u043e \u043f\u043e\u044f\u0432\u044f\u0442\u0441\u044f",
        "Orbit Command",
        "black-hole",
        "particle-canvas",
    ]
    present = [item for item in forbidden if item in html]
    assert not present, present

    for route in [
        "overview",
        "portfolio",
        "positions",
        "trades",
        "history",
        "signals",
        "bias",
        "analytics",
        "risk",
        "lab",
        "sources",
        "storage",
        "settings",
    ]:
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
