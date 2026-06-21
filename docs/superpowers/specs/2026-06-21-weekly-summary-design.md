# Weekly Cyber Summary — Design

*Date: 2026-06-21*

## Goal

A weekly companion to the daily cyber briefing pipeline. Every Sunday at 12:00
it reads the past 7 daily briefing backups, extracts the **curated news
stories** (everything *below* the `## 🔒 Vulnerabilities` section — the
published CVEs are deliberately ignored), asks Claude to merge recurring
stories, rank them, and write fresh 1–2 sentence summaries, then delivers a
"Weekly Cyber Summary — \<Mon\> to \<Sun\>" note to Bear with the same
always-on markdown backup the daily job uses.

This mirrors how the daily job works: headless, run by launchd, Claude for the
intelligence, Bear for delivery, markdown backup as the safety net.

## Decisions taken (with the user, 2026-06-21)

- **Data source:** the daily markdown backups in `~/cyberbriefing-output/`.
  No Bear MCP dependency; fits the headless launchd model exactly like the
  daily job. (The screenshot spec described using Bear MCP tools; we read the
  backups instead because they *are* the daily briefings and need no MCP at
  run time.)
- **Processing:** Claude (same as the daily job) — merges recurring stories,
  ranks, and writes fresh summaries. One extra API call per week.

## Architecture

New entry point `weekly.py` at the repo root, plus a `weekly/` package. Three
existing pieces are reused unchanged:

- `delivery/bear.py` → `deliver_to_bear(title, body, tags)`
- the Anthropic client pattern in `prioritiser/scorer.py` (model from config,
  `ANTHROPIC_API_KEY`, ```` ```json ```` fence stripping, JSON parse)
- `db/state.py` for idempotency (new weekly helpers added)

```
weekly.py              ← Entry point (CLI: --dry-run, -v)
weekly/
  __init__.py
  reader.py            ← Find the daily backups in ~/cyberbriefing-output/,
                          select the Mon→Sun window, parse each into
                          below-the-line stories
  summariser.py        ← Claude call: dedupe across days, rank, write
                          1–2 sentence summaries → ordered top 8–12
  prompt.txt           ← Weekly rubric
  formatter.py         ← Ordered stories → markdown body (no title heading)
```

## Data model

A parsed story (from `reader.py`):

```python
{
    "date": "2026-06-19",          # source briefing date
    "section": "Critical",          # Critical / Notable / Radar / Britain
    "headline": "F5 issues out-of-band ...",
    "sources": [                     # (name, url) pairs from the links line
        ("The Hacker News", "https://thehackernews.com/..."),
        ("BleepingComputer", "https://www.bleepingcomputer.com/..."),
    ],
    "paragraph": "NGINX is one of the most widely deployed ...",
    "score": 18.1,                  # float or None if absent
}
```

A summarised story (from `summariser.py`, what the formatter renders):

```python
{
    "headline": "...",
    "sources": [("name", "url"), ...],   # union of merged stories' sources
    "summary": "1–2 sentence why-it-matters",
}
```
ordered most → least important.

## Data flow

1. **Read** (`reader.py`)
   - Glob `~/cyberbriefing-output/Cyber Briefing _ *.md`.
   - Parse the date from each filename (`Cyber Briefing _ YYYY-MM-DD.md`).
   - Keep files whose date is within the Mon→Sun window. Window is derived from
     the run date: `sunday = run_date`, `monday = run_date - 6 days`. (Run date
     defaults to today; `reader.py` accepts an injectable date for testing.)
   - For each kept file, split on `## ` headers, **drop** the
     `🔒 Vulnerabilities` section, and parse every `### ` block in the remaining
     sections into the parsed-story dict above.
     - Story block grammar (from the real backups): a `### headline` line, then
       a links line `[name](url) · [name](url)`, then one or more paragraph
       lines, then a `*Score: N*` line (and optionally `· Tier`). The Britain
       section is headline-only bullets — captured as headline with empty
       paragraph/score.
   - Return `(stories, n_briefings)`.

2. **Summarise** (`summariser.py`)
   - Send all stories to Claude with `weekly/prompt.txt`.
   - Claude merges duplicates (shared source URL + semantic), ranks by
     importance (existing score + active-exploitation priority, with a boost
     for blogs, cheat sheets, and new tool capabilities per the spec), and
     returns the top 8–12 summarised stories as JSON.
   - Same client/parse/retry shape as `scorer.py` (single call — the weekly
     input is well under the token budget, so no chunking needed; one retry on
     failure).

3. **Format** (`formatter.py`)
   - Opening line: `*Reviewed N briefings · M stories this week.*`
   - Per story, ordered most → least important:
     ```
     ### <headline>
     [name](url) · [name](url)
     <summary>
     ```
   - No title heading in the body (Bear takes the title from the `title=`
     param; the markdown backup adds its own `# title`, as it already does).

4. **Deliver** (reuse `delivery/bear.py`)
   - `deliver_to_bear(title, body, ["security/briefing/weekly"])`.
   - Title: `Weekly Cyber Summary — <monday> to <sunday>` (e.g.
     `Weekly Cyber Summary — 2026-06-15 to 2026-06-21`).
   - Markdown backup always written by `deliver_to_bear`.

5. **Mark delivered** (`db/state.py`)
   - New `mark_weekly_delivered()` / `was_weekly_delivered_this_week()` using a
     `_weekly_delivered` slot in `scraper_runs` (same approach as the daily
     `_briefing_delivered` slot). "This week" = same ISO year+week as now.

## Scheduling & robustness

- New launchd plist `com.cyberbriefing.weekly.plist`, same hardening as the
  daily one: `LimitLoadToSessionType = Aqua`, `ProcessType = Interactive`,
  `RunAtLoad = false`, program wrapped in `caffeinate -is`.
- Two `StartCalendarInterval` slots: **Sunday 12:00** primary,
  **Sunday 13:30** idempotent fallback. `weekly.py` exits cleanly at the top if
  `was_weekly_delivered_this_week()`.
- No `pmset` wake needed — at midday the Mac mini is fully awake (the dark-wake
  EBADF problem is a 06:00-only issue).
- Logs to `/tmp/cyberbriefing-weekly.log` / `.err`.

## Error handling

- **Short week** (fewer than 7 backup files present): summarise whatever is
  there and report the actual count in the opening line. Not a failure.
- **Zero stories** found across all files: write
  `FAILURE-weekly-<date>.md` to `~/cyberbriefing-output/` and exit non-zero,
  mirroring the daily all-sources-failed alarm. The 13:30 fallback retries.
- **Claude failure** (API error / unparseable JSON after one retry): log,
  write `FAILURE-weekly-<date>.md`, exit non-zero. Fallback retries.
- **No `ANTHROPIC_API_KEY`**: log and exit non-zero (same as daily).

## Change to existing code

- `delivery/bear.py`: bump `MARKDOWN_RETENTION_DAYS` from 7 → 10 so a
  slightly-late daily run or a DST shift never prunes Monday's backup before
  Sunday reads it. (7 days is technically sufficient for a Mon→Sun window read
  at Sunday midday, but 10 gives safe margin.)

## Testing

- `reader.py`: parse against the real backup format using a fixture copied from
  an existing `~/cyberbriefing-output/` file; assert the Vulnerabilities section
  is excluded, story fields parse correctly (headline, multi-source links,
  paragraph, score), and Britain headline-only bullets are captured.
- Date-window selection: given a fixed run date and a set of filenames, the
  correct Mon→Sun files are chosen.
- `formatter.py`: given summarised stories, renders the expected markdown
  (opening line, `###` headlines, source links, no title heading).
- `summariser.py`: parse/clean a representative Claude JSON response (mirrors
  the `scorer.py` fence-stripping tests if present); the live API call is not
  unit-tested.
- `weekly.py --dry-run`: prints to stdout, makes no state changes, like the
  daily job.

## Out of scope (YAGNI)

- Re-scoring stories from scratch (the daily scores are reused as ranking
  input).
- Reading the Vulnerabilities / CVE section (explicitly ignored).
- Any Bear MCP integration.
- A monthly/other-cadence summary.
