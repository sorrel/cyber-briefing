"""Format the summarised week into a Bear-ready markdown note.

No title heading is emitted in the body — Bear takes the title from the
x-callback-url title parameter, and the markdown backup adds its own heading.
"""

from datetime import date

WEEKLY_TAG = "security/briefing/weekly"


def format_weekly(
    summarised: list[dict],
    n_briefings: int,
    n_stories: int,
    monday: date,
    sunday: date,
) -> tuple[str, str, list[str]]:
    """Return (title, body, tags) for the weekly summary note."""
    title = f"Weekly Cyber Summary — {monday.isoformat()} to {sunday.isoformat()}"

    lines = [
        f"*Reviewed {n_briefings} briefings · {n_stories} stories this week.*",
        "",
    ]
    for story in summarised:
        lines.append(f"### {story['headline']}")
        source_links = [f"[{name}]({url})" for name, url in story["sources"] if url]
        if source_links:
            lines.append(" · ".join(source_links))
        if story["summary"]:
            lines.append(story["summary"])
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    return title, body, [WEEKLY_TAG]
