from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    html = DASHBOARD.read_text(encoding="utf-8")
    desktop_markers = [
        "@media (min-width: 1024px)",
        "grid-template-columns: 272px minmax(0, 1fr)",
        "sidebar { transform: none !important; }",
        "cols-3",
        "cols-4",
    ]
    tablet_markers = [
        "@media (min-width: 768px) and (max-width: 1023px)",
        "repeat(2, minmax(0, 1fr))",
    ]
    mobile_markers = [
        "@media (max-width: 767px)",
        "bottom-nav",
        "MOBILE_NAV",
        "more-sheet",
        "grid-template-columns: repeat(5, minmax(0, 1fr))",
        "min-height: 44px",
        "overflow-x: visible",
        "data-label",
        "mobile-cards",
    ]
    missing = [item for item in desktop_markers + tablet_markers + mobile_markers if item not in html]
    assert not missing, missing
    print({"dashboard_responsive": "ok"})


if __name__ == "__main__":
    main()
