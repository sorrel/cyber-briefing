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

## Recurring 06:00 network-failure bug (investigation notes)

**Context:** Runs on a 24/7 mains-powered Mac mini. `pmset` has `sleep 0`. Daemon fires at 06:00 and occasionally fails to reach the network despite the machine being fully awake.

**Already implemented fixes** (these hypotheses are resolved):
- Stale DNS / dead resolver: DNS flush via `dscacheutil` + `killall -HUP mDNSResponder` implemented in `_flush_dns()`.
- IPv6 timing: `_probe_once()` forces `AF_INET`, avoiding happy-eyeballs delay.
- Process drift over 20+ h: daemon now calls `sys.exit(0)` after each run; launchd restarts it fresh each day.
- Per-attempt exception logging: `_probe_for()` logs the exception type and message on every failed probe.

**Confirmed instance (3 May 2026): `EBADF` + broken restart mechanism**

**Symptom:** All network probes failed with `OSError: [Errno 9] Bad file descriptor` at 06:00. After the DNS flush + 90s retry window also failed, the daemon called `_restart_for_fresh_state()` which used `os.execve`. The `os.execve` call failed silently (likely because FDs were already EBADF, so the exec itself couldn't proceed, and the traceback couldn't write to stderr either). launchd restarted the process cleanly — but without `CYBERBRIEFING_RUN_NOW` in the env — so it went back to sleep for 23h57m and skipped that day's briefing entirely.

**Root cause of EBADF:** The OS network stack (not just the Python process's FDs) enters a broken state at 06:00. The EBADF is not caused by stale FDs within a long-running process — confirmed because even a freshly launchd-restarted process immediately exhibits the same error. Whatever triggers this (see hypotheses below) affects the entire machine's socket layer transiently around 06:00.

**Fix applied (3 May 2026):** Replaced `os.execve` in `_restart_for_fresh_state()` with a flag file (`~/.cyberbriefing/run-now`) + `sys.exit(0)`. `main()` checks for the flag file on startup and deletes it if present, setting `run_now=True`. This survives launchd restarts reliably regardless of exec failures or FD state.

**Fix applied (4 May 2026): post-restart probe window too short**

**Symptom (4 May 2026):** Daemon ran at 06:00, got EBADF on all probes. DNS flush also failed. Called `_restart_for_fresh_state()` — flag file written, process exited, launchd restarted cleanly. Fresh process (`run_now=True`) began probing at 06:02:14 and was given only 30 seconds before giving up. EBADF persisted in the fresh process too. Gave up at 06:02:44, slept until 06:00 tomorrow. Briefing skipped for the second consecutive day.

**Key insight:** The previous hypothesis ("clears immediately on process restart") was wrong. The EBADF reflects an OS-level network outage at 06:00, not stale FDs. The fresh process starts probing during the outage and gives up before the network recovers.

**Fix:** `_wait_for_network()` now gives the post-restart process a 2-minute sleep (let the outage clear) followed by a 10-minute probe window, rather than abandoning after 30s. This is sufficient to weather a transient OS-level network disruption without skipping the briefing.

**Remaining open hypotheses (if network failure recurs):**

1. **Something scheduled on the Mac mini touching the network at 06:00** — VPN reconnect (Tailscale, WireGuard), Little Snitch rule reload, or a custom `periodic` script. Check: `launchctl list | grep -v com.apple`. This is the most likely root cause given EBADF persists across process restarts.
2. **ISP DHCP renewal or router maintenance at 06:00** — causes a brief total network outage that macOS surfaces as EBADF on socket creation.
3. **File descriptor / socket leak after N days.** After many consecutive runs the process could hit `EMFILE`. Check: `lsof -p $PID | wc -l` on startup and after each run.

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
