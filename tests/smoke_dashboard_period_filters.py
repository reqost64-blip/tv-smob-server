from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"


def test_dashboard_period_controls_present():
    text = HTML.read_text(encoding="utf-8")
    assert "periodSwitcher" in text
    assert "Сегодня" in text
    assert "Неделя" in text
    assert "Месяц" in text
    assert "Всё время" in text
    assert "data-period" in text
    assert "period:'day'" in text


def test_dashboard_russian_labels():
    text = HTML.read_text(encoding="utf-8")
    forbidden = [
        "Signal Board",
        "Risk Control",
        "Storage Health",
        "Signal Sources",
        "Signal Accuracy",
        "No signals processed yet.",
        "Strategy Lab",
    ]
    for item in forbidden:
        assert item not in text


if __name__ == "__main__":
    test_dashboard_period_controls_present()
    test_dashboard_russian_labels()
    print("ok")
