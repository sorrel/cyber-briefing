# Cyber Briefing Tool — Claude Code Context

## What this is

A Python pipeline that runs daily to produce a prioritised cybersecurity briefing, delivered to Bear Notes. It gathers from 17+ sources (APIs, RSS feeds, scrapers), scores each item using Claude, and formats a tiered markdown document.

## Running it

```bash
# Always use uv
uv run python briefing.py --dry-run     # Full pipeline → stdout (no state changes)
uv run python briefing.py --gather-only # Collect only, mark seen, no scoring
uv run python briefing.py --stats       # Show DB stats by source
uv run python briefing.py               # Real run → Bear Notes

# Daemon (long-running, sleeps until 06:00)
uv run python daemon.py                 # Runs daily at 06:00, managed by launchd
```

## Architecture

```
briefing.py          ← Entry point (CLI: --dry-run, --gather-only, --stats, -v)
config.yaml          ← All source URLs, scoring weights, thresholds (edit me)
prioritiser/
  prompt.txt         ← Claude scoring rubric (edit me to tune output)
  scorer.py          ← Claude API call; returns scored JSON
  clusterer.py       ← Merges items with same cluster_id
collectors/
  rss.py             ← Generic RSS/Atom for all feed sources
  cisa_kev.py        ← CISA Known Exploited Vulnerabilities catalogue
  nvd.py             ← NVD CVE API (CVSS ≥ 7.0 filter)
  hackerone.py       ← HackerOne Hacktivity (requires auth)
  github_advisories.py ← GitHub GraphQL advisories
  enisa_scraper.py   ← ENISA publications scraper (24h interval)
  ico_scraper.py     ← ICO enforcement actions scraper (weekly)
delivery/
  formatter.py       ← Converts scored items → markdown (title, body, tags)
  bear.py            ← Delivers to Bear Notes (x-callback-url if Bear running → AppleScript → markdown file)
db/
  state.py           ← SQLite at ~/.cyberbriefing/state.db; tracks seen items + scraper schedules
```

## Pipeline flow

1. **Gather**: All enabled collectors run; items filtered against state.db (dedup)
2. **Score**: Up to 150 most-recent unseen items sent to Claude with prompt.txt
3. **Cluster**: Items sharing a cluster_id are collapsed (highest score wins)
4. **Format**: Tiered markdown — Critical / Notable / Radar / Britain
5. **Deliver**: Bear Notes (real run) or stdout (--dry-run)
6. **Mark seen**: All gathered items written to state.db

## Tiers

| Tier | Composite score | Render style |
|------|----------------|--------------|
| Critical | ≥ 17 | Full: heading, source, annotation, score |
| Notable | ≥ 13 | Full |
| Radar | ≥ 10 | Full |
| Britain | < 10 but geographic ≥ 4 | Headline-only bullet list |
| Excluded | < 10, not UK/EU | Not shown |

## Composite score formula

`(geographic × 1.0) + (domain × 1.5) + (actionability × 1.2) + (novelty × 0.8)`

Max: 22.5. Weights tuned for a UK-based appsec professional.

## Adding or removing sources

Edit `config.yaml`:
- **RSS feeds**: add under `sources.rss_feeds` with `url`, `category`, `source_name`
- **Scrapers**: add under `sources.scrapers` with `check_interval_hours`
- **APIs**: each has its own collector module
- Add the source slug → display name mapping in `delivery/formatter.py` `_pretty_source()`

## Key tuning levers

| What | Where |
|------|-------|
| Scoring weights | `config.yaml` → `scoring.weights` |
| Score thresholds (tiers) | `config.yaml` → `scoring.threshold` + `prompt.txt` tier definitions |
| Max items in briefing | `config.yaml` → `scoring.max_items` |
| Max items sent to Claude | `config.yaml` → `scoring.max_score_input` |
| Scoring rubric / source guidance | `prioritiser/prompt.txt` |
| Section headers / render style | `delivery/formatter.py` |

## Scheduling

`daemon.py` is a long-running process that sleeps until 06:00, runs the briefing, then sleeps until the next day. Managed by launchd with `RunAtLoad` + `KeepAlive` — starts at login and restarts if it crashes. This avoids launchd TCC/network issues that affect cron-style scheduling from `~/Documents/`.

```bash
# Install
cp com.cyberbriefing.daily.plist ~/Library/LaunchAgents/
# Edit __PROJECT_DIR__ placeholder to actual path
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Restart after changes
launchctl bootout gui/$(id -u)/com.cyberbriefing.daily
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Logs
tail -f /tmp/cyberbriefing.log   # daemon status
tail -f /tmp/cyberbriefing.err   # pipeline output
```

## Bear delivery bug — investigation and fix (30 April 2026)

**Symptom:** Briefing pipeline ran cleanly at 06:04, logs said "Delivered to Bear via x-callback-url", but the note did not appear until the user opened their Mac later that morning.

**Root cause:** `open bear://x-callback-url/create?...` returns exit code 0 as soon as macOS dispatches the URL — before Bear processes it. When Bear is not already running, `open` cold-launches it. If the screen is locked or the user is away, Bear may not properly handle the callback during startup (URL dropped or silently queued). The code treated `open` exit 0 as confirmed delivery, so no fallback was triggered. Confirmed by `ps -eo lstart,command | grep Bear`: Bear process started at exactly 06:04:27, the same instant as the `open` call.

**Fixes applied (30 April 2026, Claude claude-sonnet-4-6):**

1. **Bear running pre-check** (`delivery/bear.py`): `deliver_to_bear()` now calls `pgrep -x Bear` before attempting x-callback-url. If Bear is not running, it skips x-callback-url entirely and goes straight to AppleScript. `tell application "Bear"` launches the app *and blocks until it is ready*, eliminating the timing race.

2. **Always-on markdown backup** (`delivery/bear.py`): After any successful Bear delivery (x-callback-url or AppleScript), `_write_markdown_file()` is also called. A dated `.md` file is always written to `~/cyberbriefing-output/`, so the briefing is never silently lost even if Bear fails.

## Recurring 06:00 network-failure bug — resolved (4 May 2026)

**Root cause:** The network probe itself was the bug. The network is always available at 06:00; the EBADF errors from `socket.getaddrinfo` / `socket.create_connection` are false positives — the probe code reports failure regardless of actual connectivity, and the conditions when it succeeds vs. fails are identical. Every attempt to remediate (DNS flush, process restart, extended retry windows) was fighting a symptom that didn't exist.

**Fix (4 May 2026):** Removed the network probe entirely. The daemon now just runs the briefing at 06:00 without any pre-flight connectivity check. Genuine network failures are handled by the collectors' own error handling and the `_run_briefing()` retry loop.

**History of failed attempts to fix the probe (kept for context):**

- *Stale DNS hypothesis:* Added DNS flush via `dscacheutil` + `killall -HUP mDNSResponder`. Did not help — probe still failed.
- *IPv6 timing hypothesis:* `_probe_once()` forced `AF_INET`. Did not help.
- *Stale process FDs hypothesis (3 May 2026):* Added `_restart_for_fresh_state()` — flag file + `sys.exit(0)` so launchd restarts with fresh FDs. Flag file mechanism worked, but fresh process also got EBADF. Network was fine throughout.
- *Post-restart window too short (4 May 2026 — morning):* Extended post-restart probe to 2-minute sleep + 10-minute window. Immediately superseded by removing the probe entirely.

## Secrets

Uses `.env` file (gitignored). Required keys:
- `ANTHROPIC_API_KEY` — for Claude scoring
- `HACKERONE_USERNAME` / `HACKERONE_TOKEN` — optional, for HackerOne collector
- `GITHUB_TOKEN` — optional, for GitHub Advisories collector

## State DB

SQLite at `~/.cyberbriefing/state.db`:
- `seen_items` — every gathered item (id = SHA-256 of URL), tracks `included_in_briefing`
- `scraper_runs` — last-checked timestamp per scraper source

## Common issues

- **Empty briefing**: run `--stats` to check item counts; run `--gather-only` to reset "seen" state for debugging
- **Bear not opening / note missing**: a markdown backup is *always* written to `~/cyberbriefing-output/` after every delivery attempt — check there first
- **ENISA/ICO scraper returning zero items**: site may have been redesigned; check the scraper HTML selectors

## Britain section

Items with geographic relevance ≥ 4 (UK/EU) that score below 10 overall are shown as a headline-only bullet list at the bottom of the briefing.

**The Register** is configured with nuanced scoring guidance in `prompt.txt`:
- Genuine appsec findings or UK breaches → scores normally, can appear in main tiers
- Opinion/commentary/generic tech news → low actionability (1–2), likely falls into Britain section
- Stories already covered by a more technical source (BleepingComputer, NCSC, etc.) → clustered under the higher-quality primary, not promoted as a standalone Register item
- Unique UK context that no other source covers → promoted to main tiers even if from The Register
