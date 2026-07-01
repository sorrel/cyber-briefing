from datetime import date
from pathlib import Path

from weekly.reader import parse_briefing_text, select_week_files, read_week

FIXTURE = """# Cyber Briefing — 2026-06-19

#security/briefing/daily

*3 items · Sources: BleepingComputer, CISA KEV, The Hacker News*

---

## 🔒 Vulnerabilities

### CISA KEV: Splunk file create/truncate — patch by 2026-06-21
[CISA KEV](https://nvd.nist.gov/vuln/detail/CVE-2026-20253)
*Score: 17.4 · Critical*

---

## 🔴 Critical — act on these

### F5 issues out-of-band critical patches for two NGINX RCE vulnerabilities
[The Hacker News](https://thehackernews.com/2026/06/f5.html) · [BleepingComputer](https://www.bleepingcomputer.com/news/security/f5.html)
NGINX is one of the most widely deployed web servers globally and two critical flaws enable unauthenticated RCE. Patch immediately.
*Score: 18.1*

---

## 🟡 Notable — worth reading

### Estonia to auto-quarantine all .ru inbound email to government officials
[TLDR Infosec](https://therecord.media/estonia-quarantine-russian-emails)
A notable policy measure with template value for other EU/NATO governments.
*Score: 15.7*

---

## 🇬🇧 Britain
- [ICO fines a UK firm £2m over a breach](https://ico.org.uk/action) · *ICO*
"""


def test_vulnerabilities_section_is_excluded():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    assert all("Splunk" not in s["headline"] for s in stories)
    assert all(s["section"] != "Vulnerabilities" for s in stories)


def test_standard_story_parses_all_fields():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    f5 = next(s for s in stories if s["headline"].startswith("F5 issues"))
    assert f5["date"] == "2026-06-19"
    assert f5["section"] == "Critical"
    assert f5["score"] == 18.1
    assert ("The Hacker News", "https://thehackernews.com/2026/06/f5.html") in f5["sources"]
    assert ("BleepingComputer", "https://www.bleepingcomputer.com/news/security/f5.html") in f5["sources"]
    assert "unauthenticated RCE" in f5["paragraph"]


def test_britain_bullet_parses_as_headline_only():
    stories = parse_briefing_text(FIXTURE, "2026-06-19")
    ico = next(s for s in stories if "ICO fines" in s["headline"])
    assert ico["section"] == "Britain"
    assert ico["score"] is None
    assert ico["paragraph"] == ""
    assert ico["sources"] == [("ICO", "https://ico.org.uk/action")]


def test_select_week_files_picks_monday_to_sunday(tmp_path):
    # Week containing Sunday 2026-06-21 is Mon 2026-06-15 .. Sun 2026-06-21
    names = [
        "Cyber Briefing _ 2026-06-14.md",  # previous Sunday — excluded
        "Cyber Briefing _ 2026-06-15.md",  # Monday — included
        "Cyber Briefing _ 2026-06-19.md",  # included
        "Cyber Briefing _ 2026-06-21.md",  # Sunday — included
        "FAILURE-2026-06-20.md",            # not a briefing — ignored
    ]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")
    paths, monday, sunday = select_week_files(tmp_path, date(2026, 6, 21))
    got = [p.name for p in paths]
    assert got == [
        "Cyber Briefing _ 2026-06-15.md",
        "Cyber Briefing _ 2026-06-19.md",
        "Cyber Briefing _ 2026-06-21.md",
    ]
    assert monday == date(2026, 6, 15)
    assert sunday == date(2026, 6, 21)


def test_select_week_files_monday_picks_previous_week(tmp_path):
    # On the laptop the summary runs Monday 10:00. A Monday run must summarise
    # the week that just ENDED (Mon 2026-06-15 .. Sun 2026-06-21), not the new
    # week that starts today (which is empty).
    names = [
        "Cyber Briefing _ 2026-06-15.md",  # previous Monday — included
        "Cyber Briefing _ 2026-06-19.md",  # included
        "Cyber Briefing _ 2026-06-21.md",  # previous Sunday — included
        "Cyber Briefing _ 2026-06-22.md",  # the run day (new week) — excluded
    ]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")
    paths, monday, sunday = select_week_files(tmp_path, date(2026, 6, 22))
    got = [p.name for p in paths]
    assert got == [
        "Cyber Briefing _ 2026-06-15.md",
        "Cyber Briefing _ 2026-06-19.md",
        "Cyber Briefing _ 2026-06-21.md",
    ]
    assert monday == date(2026, 6, 15)
    assert sunday == date(2026, 6, 21)


def test_read_week_counts_briefings(tmp_path):
    (tmp_path / "Cyber Briefing _ 2026-06-19.md").write_text(FIXTURE, encoding="utf-8")
    stories, n_briefings, monday, sunday = read_week(tmp_path, date(2026, 6, 21))
    assert n_briefings == 1
    assert len(stories) == 3  # F5, Estonia, ICO — Splunk vuln excluded
