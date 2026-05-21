from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    html = DASHBOARD.read_text(encoding="utf-8")
    markers = [
        "@media (max-width: 1023px)",
        "@media (max-width: 768px)",
        ".sidebar { display: none; }",
        "bottom-nav",
        "MOBILE_NAV",
        "grid-template-columns: repeat(5, minmax(0, 1fr))",
        "min-height: 58px",
        "min-height: 44px",
        ".desktop-table { display: none; }",
        ".mobile-cards { display: block; }",
        ".cols-2, .cols-3, .cols-4, .wide-grid { grid-template-columns: 1fr; }",
        "overflow-x: auto",
    ]
    missing = [item for item in markers if item not in html]
    assert not missing, missing
    print({"dashboard_responsive": "ok"})


if __name__ == "__main__":
    main()
