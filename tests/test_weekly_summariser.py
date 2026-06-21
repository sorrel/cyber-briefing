from weekly.summariser import build_payload, parse_response


def _stories():
    return [
        {"date": "2026-06-19", "section": "Critical",
         "headline": "F5 NGINX RCE", "sources": [("THN", "https://thn/f5")],
         "paragraph": "Critical NGINX flaws.", "score": 18.1},
        {"date": "2026-06-20", "section": "Notable",
         "headline": "F5 NGINX patches out-of-band", "sources": [("BC", "https://bc/f5")],
         "paragraph": "Same NGINX story, day two.", "score": 16.0},
        {"date": "2026-06-18", "section": "Notable",
         "headline": "Estonia quarantines .ru email", "sources": [("TLDR", "https://tldr/ee")],
         "paragraph": "Policy measure.", "score": 15.7},
    ]


def test_build_payload_assigns_ids_and_hides_nothing_needed():
    payload = build_payload(_stories())
    assert [p["id"] for p in payload] == [0, 1, 2]
    assert payload[0]["headline"] == "F5 NGINX RCE"
    assert payload[0]["score"] == 18.1


def test_parse_response_merges_sources_by_id():
    response = """```json
{"stories": [
  {"headline": "F5 patches critical NGINX RCEs", "summary": "Patch now.", "source_ids": [0, 1]},
  {"headline": "Estonia to quarantine .ru email", "summary": "Watch this.", "source_ids": [2]}
]}
```"""
    result = parse_response(response, _stories())
    assert len(result) == 2
    assert result[0]["headline"] == "F5 patches critical NGINX RCEs"
    assert result[0]["summary"] == "Patch now."
    # Sources from both merged stories, de-duplicated, order preserved.
    assert result[0]["sources"] == [("THN", "https://thn/f5"), ("BC", "https://bc/f5")]
    assert result[1]["sources"] == [("TLDR", "https://tldr/ee")]


def test_parse_response_skips_unknown_ids():
    response = '{"stories": [{"headline": "H", "summary": "S", "source_ids": [0, 99]}]}'
    result = parse_response(response, _stories())
    assert result[0]["sources"] == [("THN", "https://thn/f5")]
