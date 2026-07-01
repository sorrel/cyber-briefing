"""Convert the briefing's Bear-flavoured markdown into Slack Block Kit.

Both pipelines (daily `format_briefing`, weekly `format_weekly`) emit the same
`(title, body, tags)` markdown shape. This module renders that markdown into a
list of "block groups": the first group is the parent chat.postMessage blocks,
and each subsequent group is posted as a threaded reply so a long briefing never
exceeds Slack's per-message limits.

Correctness trap: our markdown uses `*...*` for *italic* (Score lines, source
captions), but Slack mrkdwn treats `*...*` as *bold* and uses `_..._` for
italic. `_md_inline_to_mrkdwn` remaps this: it protects real bold (`**...**`)
as sentinels, converts single-delimiter emphasis to Slack italic, then restores
the bold as Slack `*...*`.
"""

import re

# Slack limits (kept conservative). A message may hold <=50 blocks; a section
# block's text is capped at 3000 chars; a header block at 150 plain-text chars.
MAX_BLOCKS_PER_MESSAGE = 45
MAX_SECTION_CHARS = 2900
MAX_HEADER_CHARS = 150

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")


def _md_inline_to_mrkdwn(text: str) -> str:
    """Convert inline markdown (links, bold, italic) to Slack mrkdwn."""
    text = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)

    # Protect bold spans as sentinels so the italic pass can't split them.
    bolds: list[str] = []

    def _stash_bold(m: "re.Match") -> str:
        bolds.append(m.group(1) or m.group(2))
        return f"\x00{len(bolds) - 1}\x00"

    text = _BOLD_RE.sub(_stash_bold, text)
    text = _ITALIC_RE.sub(lambda m: f"_{m.group(1) or m.group(2)}_", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"*{bolds[int(m.group(1))]}*", text)
    return text


def _header_block(title: str) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": title[:MAX_HEADER_CHARS], "emoji": True},
    }


def _section_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into <=limit-char pieces on line boundaries.

    A single line longer than `limit` is hard-split so no piece ever exceeds it.
    """
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                pieces.append(current)
            current = line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _chunk(blocks: list[dict], size: int) -> list[list[dict]]:
    if not blocks:
        return [[]]
    return [blocks[i:i + size] for i in range(0, len(blocks), size)]


def markdown_to_block_groups(title: str, body: str) -> list[list[dict]]:
    """Render the briefing markdown into ordered Slack block groups."""
    blocks: list[dict] = [_header_block(title)]
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip("\n")
        buffer.clear()
        if text.strip():
            for piece in _split_text(text, MAX_SECTION_CHARS):
                blocks.append(_section_block(piece))

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "---":
            flush()
            blocks.append({"type": "divider"})
        elif stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            buffer.append(f"*{_md_inline_to_mrkdwn(heading)}*")
        else:
            buffer.append(_md_inline_to_mrkdwn(line))
    flush()

    return _chunk(blocks, MAX_BLOCKS_PER_MESSAGE)
