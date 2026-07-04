# Cyber Briefing Tool — Claude Code Context

## What this is

A Python pipeline that runs daily to produce a prioritised cybersecurity briefing, delivered to Bear Notes or a Slack channel (configurable). It gathers from 17+ sources (APIs, RSS feeds, scrapers), scores each item using Claude, and formats a tiered markdown document.

## Deployment environment

This tool runs on **two machines from the same git repo**. Per-machine differences are isolated to a gitignored `config.local.yaml` (delivery method) and separate launchd plists (schedule + paths); the shared code and `config.yaml` are identical on both.

- **Home Mac mini** (user `duncan`, on 24/7) — delivers to **Bear**. Has a `config.local.yaml` that sets `delivery.method: bear` explicitly (matching the committed default) and overrides `scoring.model` to **Haiku 4.5** (~3× cheaper than the committed Sonnet 4.6). Uses `com.cyberbriefing.{daily,weekly}.plist` (06:15 daily, **Monday–Saturday** — no Sunday daily; Sunday weekly) plus the `pmset` wake. **All the always-on / dark-wake / EBADF / `pmset` reasoning in this document applies to THIS machine** — no sleep/wake, no Wi-Fi roaming, no lid-close.
- **Work laptop** (user `duncanhurwood`) — delivers to **Slack**. Has a `config.local.yaml` overriding `delivery.method` (Slack) and the scoring model. Uses `com.cyberbriefing.{daily,weekly}.laptop.plist` (08:40 **weekdays**; **Monday** weekly), and **no `pmset`**: a closed laptop can't be woken reliably, so it relies on launchd running a *missed* calendar job on the next wake ("08:40, or first wake after"). Slack delivery needs **1Password unlocked** at run time — the local-env FIFO streams no token while locked. A locked fire no longer hangs (it did until 2 Jul 2026): the `.env` load is now time-bounded, and a real run whose secrets never arrive fails fast with a `secrets_unavailable` marker so the next fire retries (see *1Password FIFO env-load hang* below).

Before applying any scheduling/network reasoning, check which machine you mean: the Mac-mini sections below assume always-on; the laptop sleeps, roams, and closes its lid.

## Per-machine config (`config.local.yaml`)

`config_loader.load_config()` reads `config.yaml` and deep-merges an optional, gitignored `config.local.yaml` over it (a nested override like `delivery.method` replaces just that key, leaving `delivery.slack.channel` intact). Both `briefing.py` and `weekly_run.py` load config through it. A machine with no local file would fall back to the committed defaults, but in practice **both** machines have one and drive their delivery target and scoring model from it rather than leaning on an implicit default: the mini's sets `delivery.method: bear` + `scoring.model` to Haiku 4.5; the laptop's sets `delivery.method: slack` + its own scoring model (Sonnet 4.6). This is how one repo drives Bear + Haiku on the mini and Slack + Sonnet on the laptop, without diverging committed files or branches.

## Running it

```bash
# Always use uv
uv run python briefing.py --dry-run     # Full pipeline → stdout (no state changes)
uv run python briefing.py --gather-only # Collect only, mark seen, no scoring
uv run python briefing.py --stats       # Show DB stats by source
uv run python briefing.py               # Real run → Bear or Slack (per delivery.method)
```

## Architecture

```
briefing.py          ← Entry point (CLI: --dry-run, --gather-only, --stats, -v)
config.yaml          ← All source URLs, scoring weights, thresholds (edit me)
config.local.yaml    ← Per-machine overrides, gitignored (laptop: delivery.method slack)
config_loader.py     ← Loads config.yaml, deep-merges config.local.yaml over it
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
  dispatch.py        ← Routes (title, body, tags) to the configured delivery.method; always writes the markdown backup
  bear.py            ← Bear Notes via x-callback-url (Bear-only; backup lives in dispatch/backup now)
  slack.py           ← Slack chat.postMessage delivery (native message + threaded overflow)
  slack_format.py    ← Converts briefing markdown → Slack Block Kit groups
  backup.py          ← Always-on markdown backup to ~/cyberbriefing-output/ (read by the weekly pipeline)
db/
  state.py           ← SQLite at ~/.cyberbriefing/state.db; tracks seen items + scraper schedules
```

## Pipeline flow

1. **Gather**: All enabled collectors run; items filtered against state.db (dedup)
2. **Score**: Up to 150 most-recent unseen items sent to Claude with prompt.txt
3. **Cluster**: Items sharing a cluster_id are collapsed (highest score wins)
4. **Format**: Tiered markdown — Critical / Notable / Radar / Britain
5. **Deliver**: via `delivery.method` — Bear Notes or Slack (real run) or stdout (--dry-run); a markdown backup is always written
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

Max: 23.5. Geographic scores up to 6 (UK-specific items get an extra point over the other tiers); the other three dimensions are 1–5. Weights tuned for a UK-based appsec professional in financial services.

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
| Delivery target (bear / slack / stdout / markdown_file) | `config.yaml` → `delivery.method` (+ `delivery.slack.channel` for Slack); per-machine override in `config.local.yaml` |

## Scheduling

> This section describes the **home Mac mini** (`com.cyberbriefing.*.plist`). The **work laptop** uses `com.cyberbriefing.*.laptop.plist` — 08:40 on weekdays (`Weekday` 1–5), Monday 10:00 weekly, laptop paths, and **no `pmset`** (it relies on launchd firing the missed calendar job on the next wake, since a closed lid can't be woken). Install those the same way, substituting the `.laptop.plist` filenames. Everything else (Aqua/Interactive/`caffeinate`/idempotency) is identical.

Cron-style launchd: a fresh `briefing.py` process is spawned at each calendar slot. The schedule runs **Monday–Saturday only** (`Weekday` 1–6 on every slot); Sunday is deliberately omitted so only the weekly summary runs that day. Two slots per day:

- **06:15** — primary fire.
- **07:30** — idempotent fallback. `briefing.py` checks `state.db` (`was_delivered_today()`) and exits cleanly if today's briefing has already been delivered, so this is a no-op on good days and the only thing that runs on bad days.

The plist is hardened for correct user GUI context — this is what the previous long-running daemon got wrong:

- `LimitLoadToSessionType = Aqua` — only loads in the user GUI session, where `mDNSResponder` mach ports are usable.
- `ProcessType = Interactive` — full scheduling priority; not background-throttled.
- `RunAtLoad = false` — fires only on schedule.
- Wrapped in `caffeinate -is` — keeps the system out of any idle/sleep transition during the run.

The plist also requires the system to be in a real wake state at the schedule time. Even on the always-on Mac mini, macOS keeps the user session in a degraded "dark wake" overnight that breaks `getaddrinfo` with EBADF (see the *Recurring 06:00 EBADF bug — actual fix (9 May 2026)* section). A `pmset repeat` schedules a real user-session wake at 06:10, five minutes before the primary fire.

```bash
# One-time: schedule a real daily wake five minutes before the primary fire.
sudo pmset repeat wakeorpoweron MTWRFSU 06:10:00
# Verify
pmset -g sched

# Install (or re-install after plist edits)
launchctl bootout gui/$(id -u)/com.cyberbriefing.daily 2>/dev/null
cp com.cyberbriefing.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Manual fire (e.g. for testing)
launchctl kickstart -k gui/$(id -u)/com.cyberbriefing.daily

# Inspect context (confirm Aqua / Interactive)
launchctl print gui/$(id -u)/com.cyberbriefing.daily

# Logs
tail -f /tmp/cyberbriefing.log
tail -f /tmp/cyberbriefing.err
```

## Weekly summary 🗓️

A companion pipeline that runs **Sunday 12:00** (13:30 idempotent fallback) and rolls the week's daily briefings into one Bear note — `Weekly Cyber Summary — <Mon> to <Sun>`, tag `security/briefing/weekly`. It reads the daily markdown backups in `~/cyberbriefing-output/`, drops the Vulnerabilities (CVE) section, and asks Claude to dedupe/rank/summarise — biased towards blogs, tools and new techniques — into the top ~8–12 stories. (Backup retention was raised 7 → 10 days so Sunday always sees the full week.) The **laptop** runs this **Monday 10:00** instead; `weekly/reader.py: select_week_files` targets the **most recently completed Mon→Sun week**, so both a Sunday run (mini) and a Monday run (laptop) summarise the week that just ended — not the empty week starting today.

```bash
uv run python weekly_run.py --dry-run   # → stdout, no state changes
uv run python weekly_run.py             # → Bear or Slack (per delivery.method)
```

- **Code:** `weekly_run.py` + the `weekly/` package (`reader.py`, `summariser.py`, `prompt.txt`, `formatter.py`); reuses `delivery/bear.py` and `db/state.py`.
- **Scheduling:** `com.cyberbriefing.weekly.plist` — same Aqua/Interactive/`caffeinate` hardening as the daily, no `pmset` needed at midday. Install/inspect like the daily plist (label `com.cyberbriefing.weekly`); logs at `/tmp/cyberbriefing-weekly.{log,err}`.
- **Failure:** empty week or Claude failure → `FAILURE-weekly-<date>.md` + non-zero exit; the 13:30 fallback retries.

## Slack delivery

Set `delivery.method: slack` to deliver to a Slack channel instead of Bear.
On the multi-machine setup this lives in the laptop's gitignored
`config.local.yaml` (see *Per-machine config* above), not in the shared
`config.yaml` (which stays `bear` for the mini). Applies to both the daily
(`briefing.py`) and weekly (`weekly_run.py`) pipelines, which both route
through `delivery/dispatch.py`.

- **Auth:** `SLACK_BOT_TOKEN` (env, via the 1Password local env file). Only
  the `chat:write` bot scope is needed; the bot must be invited to the channel.
- **Channel:** `delivery.slack.channel` in `config.yaml` (a channel ID; never
  hardcoded in Python).
- **Rendering:** `delivery/slack_format.py` converts the briefing markdown to
  Slack Block Kit — note Slack's `*bold*` / `_italic_` is the inverse of our
  markdown's `*italic*`, which the converter remaps. Long briefings overflow
  into threaded replies under the parent message.
- **Backup invariant:** `delivery/dispatch.py` always writes the
  `~/cyberbriefing-output/` markdown backup for every method except `stdout`,
  because `weekly/reader.py` reads those backups. Bear/Slack posting is
  best-effort; the backup is the durable artifact and the success signal.
- **Secrets caveat:** the 1Password local env file prompts for authorization
  on first read after 1Password *locks*, and its FIFO does not support
  concurrent readers. For the unattended launchd fires to obtain the token,
  1Password must stay unlocked. The daily and weekly fire windows do not
  overlap, so the single-reader limit is not a concern.

## Bear delivery bug — investigation and fix (30 April 2026)

**Symptom:** Briefing pipeline ran cleanly at 06:04, logs said "Delivered to Bear via x-callback-url", but the note did not appear until the user opened their Mac later that morning.

**Root cause:** `open bear://x-callback-url/create?...` returns exit code 0 as soon as macOS dispatches the URL — before Bear processes it. When Bear is not already running, `open` cold-launches it. If the screen is locked or the user is away, Bear may not properly handle the callback during startup (URL dropped or silently queued). The code treated `open` exit 0 as confirmed delivery, so no fallback was triggered. Confirmed by `ps -eo lstart,command | grep Bear`: Bear process started at exactly 06:04:27, the same instant as the `open` call.

**Fixes applied (30 April 2026, Claude claude-sonnet-4-6):**

1. **Bear running pre-check** (`delivery/bear.py`): `deliver_to_bear()` now calls `pgrep -x Bear` before attempting x-callback-url. If Bear is not running, it skips x-callback-url entirely and goes straight to AppleScript. `tell application "Bear"` launches the app *and blocks until it is ready*, eliminating the timing race.

2. **Always-on markdown backup** (`delivery/bear.py`): After any successful Bear delivery (x-callback-url or AppleScript), `_write_markdown_file()` is also called. A dated `.md` file is always written to `~/cyberbriefing-output/`, so the briefing is never silently lost even if Bear fails.

## Bear AppleScript fallback removed (16 May 2026)

**Symptom:** 16 May 2026 morning briefing ran cleanly at 06:17 (markdown backup written, `state.db` marked delivered) but no note appeared in Bear. Mac had rebooted around 07:44 — almost certainly a macOS update install — and Bear had shut down with it. At 06:17, Bear briefly looked alive (`pgrep` returned true) so `open bear://...` was attempted; the OS handed the URL off (exit 0) but Bear dropped it during shutdown.

**Investigation finding (worth keeping):** Bear 2.8.1 has **no AppleScript scripting interface at all** — no `.sdef` file, no `OSAScriptingDefinition` in `Info.plist`, and `sdef /Applications/Bear.app` returns error -192. The `_deliver_via_applescript()` path with `tell application "Bear" to create note with text …` has therefore never worked; what saved every "Bear closed" morning was the markdown backup, not AppleScript. Verified directly: even from an interactive shell, `osascript -e 'tell application "Bear" to create note with text "x"'` errors with `-2740`.

**Fixes applied (16 May 2026, Claude claude-opus-4-7):**

1. **Deleted the AppleScript branch** from `delivery/bear.py`. It was dead code.
2. **Real cold-launch path for Bear:** when `pgrep` says Bear is not running, `_launch_bear_and_wait()` calls `open -ga Bear` and then polls `pgrep` until Bear has been alive for ≥2 seconds (capped at 15 s total), which clears the cold-launch URL race that motivated the original 30 April fix.
3. **Honest return value:** `deliver_to_bear()` no longer claims Bear delivery succeeded when only the markdown backup landed. The return value now reflects whether *anything* (Bear note or markdown) was preserved — markdown is enough on its own, and the 06:15 / 07:30 launchd pair already gives Bear a second attempt on bad mornings.

**What remains undetectable:** today's exact failure mode — Bear briefly alive then terminating mid-callback — cannot be caught client-side. `open` returns 0 the moment the OS accepts the URL handoff; we have no signal that Bear actually consumed it. The markdown backup in `~/cyberbriefing-output/` is the answer here, and on a normal day the user can just open that file directly.

## Recurring 06:00 EBADF bug — diagnosis and partial fix (4 May 2026, superseded 9 May — see next section)

**Symptom:** roughly half of mornings, `socket.getaddrinfo` / `socket.create_connection` returned `OSError: [Errno 9] Bad file descriptor`. Every collector failed; no briefing delivered. Other times of day were fine.

**Earlier wrong diagnoses (recorded so we don't re-explore them):**

- *Stale DNS:* added `dscacheutil` flush + `mDNSResponder` HUP. No effect.
- *IPv6 timing:* forced `AF_INET`. No effect.
- *Stale process FDs (3 May 2026):* `sys.exit(0)` to make launchd respawn with fresh FDs. Flag-file mechanism worked, but the fresh process **also** got EBADF — which ruled out our process state.
- *Post-restart window too short:* extended probe to 10 min. Same result, superseded same day.
- *"The probe is the bug" (4 May, morning):* assumed the probe was a false positive and removed it. This was wrong too — removing the probe just means the collectors hit the same EBADF instead.

**Actual root cause:** the launchd agent was being spawned in the **wrong macOS user context**. `launchctl print` showed `spawn type = daemon (3)` and a stripped `inherited environment` — i.e., a background daemon-style spawn rather than an Aqua user-GUI spawn. On macOS, `getaddrinfo` uses mach IPC to `mDNSResponder`; if the spawning context doesn't have the right mach-port access (typically because the agent isn't pinned to the Aqua session, the screen is locked, and the user session is in a degraded state), the resolver port comes back with a closed FD → EBADF on every name lookup. Even a launchd-restarted "fresh" process inherits the same bad bootstrap context, which is why restarting didn't help.

**Fix (4 May 2026, evening):**

1. **Rewrote the plist for correct user context** — added `LimitLoadToSessionType = Aqua`, `ProcessType = Interactive`, `RunAtLoad = false`, and wrapped the program in `caffeinate -is`. See the *Scheduling* section above.
2. **Replaced the long-running daemon with cron-style** `StartCalendarInterval` at 06:15 + 07:30. Each fire is a fresh process in the proper Aqua context. `daemon.py` deleted.
3. **Added idempotency** — `db.state.was_delivered_today()` / `mark_delivered_today()` (re-using the existing `scraper_runs` table); `briefing.py` exits cleanly at the top of `run_pipeline` if today's briefing is already delivered, so the 07:30 fallback is a free no-op on good days.

The previous Bear-delivery fix (markdown backup, AppleScript fallback) is unchanged. (The AppleScript fallback was later removed on 16 May 2026 — see *Bear AppleScript fallback removed* above. Markdown backup is unchanged.)

## Recurring 06:00 EBADF bug — actual fix (9 May 2026)

The 4 May plist rewrite (Aqua / Interactive / `caffeinate -is`) was necessary but **not sufficient**. On 9 May 2026 both the 06:15 and 07:30 fires hit EBADF on every source, despite `launchctl print` confirming `spawn type = interactive (4)`. Within seconds of the user touching the Mac, the same pipeline ran cleanly with the same launchd setup — proving the launchd-context theory was incomplete.

**Actual root cause:** macOS keeps the Mac mini in a reduced "dark wake" overnight even though the machine is on 24/7. In that state, user-session services like `mDNSResponder` are gated; `getaddrinfo` returns EBADF because its mach-port endpoint is not usable. `caffeinate -is` only blocks *new* idle/sleep transitions during the run — it does nothing to restore a session that is already in a degraded state when the script starts.

**Fix (9 May 2026, Claude claude-opus-4-7):**

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 06:10:00
```

A real user-session wake five minutes before the launchd fire. By 06:15 the system is fully active and `getaddrinfo` works. Persists across reboots; cancel with `sudo pmset repeat cancel`; verify with `pmset -g sched`. The 07:30 fallback remains as belt-and-braces in case anything else interferes.

The 4 May plist setup (Aqua, Interactive, caffeinate, cron-style schedule, idempotency) and the 30 April Bear-delivery fix are unchanged — both still required, just not on their own enough.

## 1Password FIFO env-load hang — diagnosis and fix (2 July 2026)

**Symptom:** the work-laptop 08:40 fire produced no briefing. Unlike a normal miss, the process had *not* exited — `ps` showed the 08:40 `briefing.py` still alive nearly an hour later, at 0% CPU, and `/tmp/cyberbriefing.{log,err}` were both **0 bytes**. Nothing had run, yet nothing had failed.

**Root cause:** secrets are delivered through a 1Password **local-env FIFO** — the `.env` is a named pipe (`prw-------`), not a regular file. `briefing.py` called `load_dotenv(".env")` at **module import time, before logging was configured** (line 23). Opening a FIFO for reading **blocks until a writer attaches**; the writer is 1Password, which needs the read authorised. At an unattended fire 1Password was locked / the auth prompt went unseen, so no writer ever came and `open()` blocked **forever** — no exception, no timeout, nothing to retry. A stack sample confirmed the process parked in a single `__open` syscall under `load_dotenv`. Because this was before logging, the logs were empty; because it was before everything, not even the markdown backup was written. (The earlier note that a locked 1Password "falls back to the markdown backup" was wrong — it hung outright.)

**Fix (2 July 2026, Claude claude-opus-4-8):**

1. **`config_loader.load_env_with_timeout()`** — bounds each `load_dotenv` with `SIGALRM` (interrupts the blocked `open()` via EINTR — no leaked fd, so the single-reader FIFO stays retryable) and retries (`30s × 2`; a fresh open re-triggers the 1Password prompt for a present user). Returns `True` if the load completed (a missing/regular file returns instantly — so the **Mac mini is unaffected**), `False` on timeout. The call **moved from import time into `main()`, after logging** — so a future failure is visible in the log, and imports (and the test suite) are no longer at the mercy of the FIFO.
2. **Fail-fast** — `briefing._secrets_blocked()`: a real delivery run whose env load timed out aborts *before* gather with a new `secrets_unavailable` FAILURE marker (accurate cause, not the misleading "scoring failed / API overloaded"), freeing the launchd slot so the fallback fire + next wake retry. `--stats`/`--gather-only` (no secrets needed) and `--dry-run` are exempt.
3. **`config_loader.arm_runtime_watchdog()`** — a daemon-thread whole-process timeout (15 min) armed at the top of `main()` in both entry points, so **any** future hang (wedged HTTP, stuck scraper) can't hold a slot for an hour. A thread, not `SIGALRM`, so it never collides with (1). Caveat: the Anthropic client's per-request read timeout is 600s with a half-size retry, so a badly degraded-API scoring run could in theory approach 15 min and be killed mid-run — the fallback fire then retries.

`weekly_run.py` got the same bounded load + watchdog (it had the identical import-time `load_dotenv`). All shared code, so both machines run it; the mini's always-unlocked 1Password never hits the timeout, so its behaviour is unchanged on normal days and strictly better on abnormal ones.

## All-sources-failed alarm

`gather_all()` returns `(new_items, total_gathered)`. A healthy run gathers hundreds of items, so `total_gathered == 0` means every collector returned nothing — virtually always a network-layer block (EBADF, Network Extension drop, DNS dead), not a genuine quiet day. When that happens, `run_pipeline()` writes a visible `FAILURE-<YYYY-MM-DD>.md` to `~/cyberbriefing-output/` and exits non-zero so launchd records the failure. Without this, a network-blocked morning was indistinguishable from a quiet news day — silently absent.

## Secrets

Uses a `.env` file (gitignored), sourced via the 1Password local env file
(values streamed on read; standard `load_dotenv` — no `op run`). Required keys:
- `ANTHROPIC_API_KEY` — for Claude scoring
- `HACKERONE_USERNAME` / `HACKERONE_TOKEN` — optional, for HackerOne collector
- `GITHUB_TOKEN` — optional, for GitHub Advisories collector
- `SLACK_BOT_TOKEN` — optional, only for `delivery.method: slack` (Slack app bot token, `chat:write` scope)

## State DB

SQLite at `~/.cyberbriefing/state.db`:
- `seen_items` — every gathered item (id = SHA-256 of URL), tracks `included_in_briefing`
- `scraper_runs` — last-checked timestamp per scraper source

## Common issues

- **Empty briefing**: run `--stats` to check item counts; run `--gather-only` to reset "seen" state for debugging
- **Bear not opening / note missing**: a markdown backup is *always* written to `~/cyberbriefing-output/` after every delivery attempt — check there first
- **`FAILURE-<date>.md` in `~/cyberbriefing-output/`**: every source returned zero items — see *All-sources-failed alarm* above; check `/tmp/cyberbriefing.err` and any Network Extension (TripMode, Little Snitch, VPN)
- **ENISA/ICO scraper returning zero items**: site may have been redesigned; check the scraper HTML selectors

## Britain section

Items with geographic relevance ≥ 4 (UK/EU) that score below 10 overall are shown as a headline-only bullet list at the bottom of the briefing.

**The Register** is configured with nuanced scoring guidance in `prompt.txt`:
- Genuine appsec findings or UK breaches → scores normally, can appear in main tiers
- Opinion/commentary/generic tech news → low actionability (1–2), likely falls into Britain section
- Stories already covered by a more technical source (BleepingComputer, NCSC, etc.) → clustered under the higher-quality primary, not promoted as a standalone Register item
- Unique UK context that no other source covers → promoted to main tiers even if from The Register
