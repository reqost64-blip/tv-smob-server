from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "@media (max-width: 768px)",
        ".sidebar { display: none; }",
        ".bottom-nav",
        "MOBILE_NAV",
        "overview",
        "trades",
        "bias",
        "signals",
        "analytics",
        ".stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr));",
        ".content-grid { grid-template-columns: 1fr; }",
        ".desktop-table { display: none; }",
        ".mobile-cards { display: block; }",
        ".compact-card",
        "min-height: 44px",
        "max-width: 100%",
    ]
    missing = [marker for marker in required if marker not in HTML]
    assert not missing, missing

    assert "more-sheet" not in HTML
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in HTML
    print({"dashboard_mobile_pages": "ok", "widths": [390, 430, 768]})


if __name__ == "__main__":
    main()
