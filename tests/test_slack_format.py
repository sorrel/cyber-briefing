"""Tests for delivery/slack_format.py."""

from delivery.slack_format import (
    markdown_to_block_groups,
    _md_inline_to_mrkdwn,
    MAX_BLOCKS_PER_MESSAGE,
    MAX_SECTION_CHARS,
    MAX_HEADER_CHARS,
)


def _section_texts(blocks):
    return [b["text"]["text"] for b in blocks if b["type"] == "section"]


def test_link_converted_to_mrkdwn():
    assert _md_inline_to_mrkdwn("[THN](https://thn/f5)") == "<https://thn/f5|THN>"


def test_italic_star_becomes_underscore():
    # Our markdown uses *...* for italic; Slack italic is _..._.
    assert _md_inline_to_mrkdwn("*Score: 18.1*") == "_Score: 18.1_"


def test_bold_double_star_survives_italic_pass():
    # **bold** -> *bold* (Slack bold) and must NOT be turned into _bold_.
    assert _md_inline_to_mrkdwn("**patch now**") == "*patch now*"


def test_url_with_underscores_is_unharmed():
    out = _md_inline_to_mrkdwn("[x](https://e.com/a_b_c)")
    assert out == "<https://e.com/a_b_c|x>"


def test_title_becomes_header_block():
    groups = markdown_to_block_groups("Cyber Briefing — 2026-07-01", "*1 items*\n")
    parent = groups[0]
    assert parent[0]["type"] == "header"
    assert parent[0]["text"]["type"] == "plain_text"
    assert parent[0]["text"]["text"] == "Cyber Briefing — 2026-07-01"


def test_long_title_truncated_to_header_limit():
    groups = markdown_to_block_groups("T" * 300, "body\n")
    assert len(groups[0][0]["text"]["text"]) == MAX_HEADER_CHARS


def test_divider_becomes_divider_block():
    body = "before\n---\nafter\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    assert any(b["type"] == "divider" for b in blocks)


def test_heading_becomes_bold_section_line():
    body = "## 🔴 Critical — act on these\n### F5 RCE\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    joined = "\n".join(_section_texts(blocks))
    assert "*🔴 Critical — act on these*" in joined
    assert "*F5 RCE*" in joined


def test_section_text_never_exceeds_limit():
    body = ("word " * 4000).strip() + "\n"   # one very long paragraph
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    for text in _section_texts(blocks):
        assert len(text) <= MAX_SECTION_CHARS


def test_overflow_splits_into_thread_groups():
    body = "content\n---\n" * 60   # ~60 sections + 60 dividers + header
    groups = markdown_to_block_groups("T", body)
    assert len(groups) > 1                       # overflowed into thread replies
    assert all(len(g) <= MAX_BLOCKS_PER_MESSAGE for g in groups)
    assert groups[0][0]["type"] == "header"      # parent still leads with the title


def test_britain_bullet_converts_link_and_italic():
    body = "- [ICO fines Acme](https://ico/x) · *ICO*\n"
    blocks = [b for g in markdown_to_block_groups("T", body) for b in g]
    joined = "\n".join(_section_texts(blocks))
    assert "<https://ico/x|ICO fines Acme>" in joined
    assert "_ICO_" in joined
