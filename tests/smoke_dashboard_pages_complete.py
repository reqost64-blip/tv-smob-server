import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    page_renderers = {
        "portfolio": "renderPortfolio",
        "positions": "renderPositions",
        "trades": "renderTrades",
        "history": "renderHistory",
        "analytics": "renderAnalytics",
        "risk": "renderRisk",
        "bias": "renderBias",
        "lab": "renderLab",
        "storage": "renderStorage",
        "signals": "renderSignals",
        "sources": "renderSources",
        "settings": "renderSettings",
    }
    for route, renderer in page_renderers.items():
        assert f"id:'{route}'" in HTML, route
        assert f"function {renderer}" in HTML, renderer
        assert f"{route}: {renderer}" in HTML, route

    required_blocks = [
        "Account snapshots",
        "openPositions",
        "tradeFilters",
        "Imported history count",
        "symbolPerformance",
        "Risk damage",
        "factorTable",
        "sourceAvailabilityBlock",
        "Strategy Lab",
        "Storage health",
        "Latest signals",
        "Endpoints status",
    ]
    missing = [marker for marker in required_blocks if marker not in HTML]
    assert not missing, missing

    forbidden = [
        "skeletonPage",
        "\u0421\u0442\u0440\u0430\u043d\u0438\u0446\u0430 \u0432 \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0435",
        "\u0414\u0430\u043d\u043d\u044b\u0435 \u0441\u043a\u043e\u0440\u043e \u043f\u043e\u044f\u0432\u044f\u0442\u0441\u044f",
    ]
    present = [marker for marker in forbidden if marker in HTML]
    assert not present, present

    subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('fs'); const html=fs.readFileSync('dashboard/index.html','utf8'); const scripts=[...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]).join('\\n'); new Function(scripts);",
        ],
        cwd=ROOT,
        check=True,
    )
    print({"dashboard_pages_complete": "ok", "pages": len(page_renderers)})


if __name__ == "__main__":
    main()
