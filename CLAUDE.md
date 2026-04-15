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
  bear.py            ← Delivers to Bear Notes (x-callback-url → AppleScript fallback)
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

## Recurring 06:00 network-failure bug (investigation notes)

**Context:** Runs on a 24/7 mains-powered Mac mini. `pmset` has `sleep 0`, so the machine is always fully awake. Ruled out: router reboot (worked fine for days before it started failing), PowerNap / lid-close behaviour (not a laptop). Daemon fires at 06:00, `_wait_for_network` (daemon.py:30) polls `www.google.com:443` every 5 s for 3600 s and always fails, then skips the day.

**Hypotheses to work through if it fails again (in rough order of likelihood):**

1. **Stale DNS / scutil state in the long-running process.** Python's `getaddrinfo` caches resolver config at startup. If `mDNSResponder` restarts overnight (common around 04:00–05:00 on macOS), a process that's been running for days can keep trying a dead resolver. Test: add DNS-only probe (`socket.getaddrinfo("www.google.com", 443)`) with exception logging, separate from `create_connection`.
2. **IPv6 happy-eyeballs delay.** `socket.create_connection` tries AAAA then A. If IPv6 routing is broken or a router advert expires overnight, each attempt waits its full 5 s timeout. Test: log `ai_family` of the successful/failing addrs; try forcing AF_INET.
3. **Daemon process drift after 20+ h idle.** The daemon holds the same Python process for days. `time.sleep(wait)` for 22 h is a long blocking call; on wake, kernel-side socket state in the process may be stale. Test: after wake, call `socket.setdefaulttimeout` and do a cheap sanity check before the polling loop; or restart the daemon via `launchctl kickstart -k` after each successful run.
4. **Process was restarted by launchd mid-run.** `KeepAlive=true` restarts on any exit. If a run crashed partway, stderr might not show it (empty stdout log supports this). Check `log show --predicate 'process == "daemon.py"'` for crash traces.
5. **Something scheduled on the Mac mini touching the network around 06:00** — `periodic` daily scripts run ~03:15 by default but a custom one could clash; VPN reconnect (Tailscale, WireGuard) at a fixed hour; Little Snitch / LuLu rule reload. Check `launchctl list | grep -v com.apple` and any login-item daemons.
6. **A previous day's `run_pipeline` leaked file descriptors / sockets**, and after N days the process hits a limit (`EMFILE`). Test: log `len(os.listdir(f"/proc/{pid}/fd"))` equivalent on mac (`lsof -p $PID | wc -l`) on startup and after each run.
7. **`time.monotonic()` drift.** On Darwin, monotonic does advance during wake-time but long sleeps can interact oddly with the 3600 s deadline. Less likely given the log shows exactly 3600 s elapsed, but worth noting.
8. **The failing poll isn't actually the network — it's something blocking before `create_connection`** (import, config load, Bear check). The log wording ("Network unavailable after 3600s") is emitted unconditionally on timeout; rule this out by adding per-attempt exception logging.

**Diagnostic patch to add before the next failure** (daemon.py:36–42): log the exception type and message on each failed attempt, and log DNS resolution separately from TCP connection. That one change will tell us which of the above it is without further guessing.

**Robustness fix independent of cause:** raise `NETWORK_TIMEOUT` to ~12 h or retry at 06:30 / 07:00 / 08:00 so a transient ~5 min outage never costs a day's briefing.

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
- **Bear not opening**: the AppleScript fallback writes a `.md` file to `~/cyberbriefing-output/` — check there
- **ENISA/ICO scraper returning zero items**: site may have been redesigned; check the scraper HTML selectors

## Britain section

Items with geographic relevance ≥ 4 (UK/EU) that score below 10 overall are shown as a headline-only bullet list at the bottom of the briefing.

**The Register** is configured with nuanced scoring guidance in `prompt.txt`:
- Genuine appsec findings or UK breaches → scores normally, can appear in main tiers
- Opinion/commentary/generic tech news → low actionability (1–2), likely falls into Britain section
- Stories already covered by a more technical source (BleepingComputer, NCSC, etc.) → clustered under the higher-quality primary, not promoted as a standalone Register item
- Unique UK context that no other source covers → promoted to main tiers even if from The Register
