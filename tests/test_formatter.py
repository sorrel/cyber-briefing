from delivery.formatter import format_briefing


def _vuln(n: int, composite: float) -> dict:
    return {
        "id": f"v{n}",
        "summary": f"CVE-2026-000{n} in a widely deployed thing",
        "tier": "notable",
        "composite": composite,
        "tags": ["security/cve"],
    }


def _vuln_source(n: int) -> dict:
    return {"id": f"v{n}", "url": f"https://kev/{n}", "source": "cisa_kev"}


def test_vulnerabilities_section_is_capped_to_top_scoring_items():
    scored = [_vuln(n, composite=20.0 - n) for n in range(1, 6)]
    all_items = [_vuln_source(n) for n in range(1, 6)]

    _, body, _ = format_briefing(scored, all_items, max_vuln_items=3)

    assert "CVE-2026-0001" in body
    assert "CVE-2026-0002" in body
    assert "CVE-2026-0003" in body
    assert "CVE-2026-0004" not in body
    assert "CVE-2026-0005" not in body


def test_dropped_vulnerabilities_do_not_leak_into_tier_sections():
    scored = [_vuln(n, composite=20.0 - n) for n in range(1, 6)]
    all_items = [_vuln_source(n) for n in range(1, 6)]

    _, body, tags = format_briefing(scored, all_items, max_vuln_items=3)

    assert body.count("### CVE-2026-") == 3
    assert "## 🟡 Notable — worth reading" not in body
    assert tags == ["security/briefing/daily", "security/briefing/security/cve"]


def test_britain_vulnerabilities_are_unaffected_by_the_cap():
    scored = [_vuln(n, composite=20.0 - n) for n in range(1, 6)]
    scored.append({
        "id": "b1",
        "summary": "ICO fines a British council",
        "tier": "britain",
        "composite": 8.0,
        "tags": [],
    })
    all_items = [_vuln_source(n) for n in range(1, 6)]
    all_items.append({"id": "b1", "url": "https://ico/1", "source": "cisa_kev"})

    _, body, _ = format_briefing(scored, all_items, max_vuln_items=3)

    assert "ICO fines a British council" in body
    assert "## 🇬🇧 Britain" in body


def test_max_vuln_items_defaults_to_three():
    scored = [_vuln(n, composite=20.0 - n) for n in range(1, 6)]
    all_items = [_vuln_source(n) for n in range(1, 6)]

    _, body, _ = format_briefing(scored, all_items)

    assert body.count("### CVE-2026-") == 3


def test_item_count_header_reflects_the_capped_briefing():
    scored = [_vuln(n, composite=20.0 - n) for n in range(1, 6)]
    all_items = [_vuln_source(n) for n in range(1, 6)]

    _, body, _ = format_briefing(scored, all_items, max_vuln_items=3)

    assert body.lstrip().startswith("*3 items ·")
