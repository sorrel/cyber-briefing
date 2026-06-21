from datetime import date

from weekly.formatter import format_weekly


def test_format_weekly_builds_title_body_tags():
    summarised = [
        {"headline": "F5 patches critical NGINX RCEs",
         "summary": "Unauthenticated RCE in a ubiquitous web server. Patch now.",
         "sources": [("The Hacker News", "https://thn/f5"), ("BleepingComputer", "https://bc/f5")]},
        {"headline": "Estonia to quarantine .ru email",
         "summary": "A policy template for other EU/NATO governments.",
         "sources": [("TLDR Infosec", "https://tldr/ee")]},
    ]
    title, body, tags = format_weekly(
        summarised, n_briefings=7, n_stories=42,
        monday=date(2026, 6, 15), sunday=date(2026, 6, 21),
    )
    assert title == "Weekly Cyber Summary — 2026-06-15 to 2026-06-21"
    assert tags == ["security/briefing/weekly"]
    assert not body.lstrip().startswith("#")  # no title heading
    assert "*Reviewed 7 briefings · 42 stories this week.*" in body
    assert "### F5 patches critical NGINX RCEs" in body
    assert "[The Hacker News](https://thn/f5) · [BleepingComputer](https://bc/f5)" in body
    assert "Unauthenticated RCE in a ubiquitous web server. Patch now." in body
