# Cyber Briefing Tool — Claude Code Context

## What this is

A Python pipeline that produces a prioritised cybersecurity briefing, delivered to Bear Notes or a Slack channel. It gathers from 38 live sources (27 RSS feeds, 7 scrapers, 4 APIs), scores each item with Claude, and formats a tiered markdown document.

## Running it

```bash
# Always use uv
uv run cyberbriefing --dry-run              # Full pipeline → stdout (no state changes)
uv run cyberbriefing --gather-only          # Collect + mark seen, no scoring or delivery
uv run cyberbriefing --stats                # DB stats by source
uv run cyberbriefing --clear-source tldrsec # Reset seen-state for one source
uv run cyberbriefing                        # Real run → Bear or Slack (per delivery.method)
uv run pytest -q                            # 157 tests, ~2s
```

**Dependency management is uv-only.** The manifest is `pyproject.toml` + `uv.lock`; upgrade with `uv lock --upgrade` and `uv sync`. There is deliberately **no `requirements.txt`** — do not add one, and don't reintroduce a pip fallback (it's in `.gitignore` to keep it from creeping back). Python version is pinned in `.python-version`. Dependabot tracks the `uv` ecosystem, not pip.

## Architecture

```
src/cyberbriefing/     ← the importable package (src layout); imports are cyberbriefing.*
  briefing.py          ← Daily entry point (console script: cyberbriefing); holds _SCRAPER_REGISTRY
  weekly_run.py        ← Weekly entry point (console script: cyberbriefing-weekly)
  config_loader.py     ← load_config (config.yaml + config.local.yaml), load_env_with_timeout, arm_runtime_watchdog
  prioritiser/
    prompt.txt          ← Claude scoring rubric (edit me to tune output)
    scorer.py           ← Claude API call; 50-item chunks, JSON-schema-constrained response
    claude_response.py  ← Shared: extract JSON from a Claude response; raises TruncatedResponse at max_tokens
    deduplicator.py     ← Cross-chunk cluster-id reconciliation (one extra Claude call over all scored items)
    dedup_prompt.txt    ← Prompt for the reconciliation pass
    clusterer.py        ← Merges items sharing a cluster_id (highest score wins)
  collectors/
    base.py            ← host_matches() — domain-boundary URL check (not substring matching)
    rss.py             ← Generic RSS/Atom for all feed sources
    cisa_kev.py        ← CISA Known Exploited Vulnerabilities catalogue
    nvd.py             ← NVD CVE API (CVSS ≥ 7.0 filter)
    hackerone.py       ← HackerOne Hacktivity (requires auth)
    github_advisories.py ← GitHub GraphQL advisories
    enisa_scraper.py   ← ENISA publications
    ico_scraper.py     ← ICO enforcement actions
    tldr_scraper.py    ← TLDR Infosec newsletter
    cloudseclist_scraper.py ← CloudSecList issues
    aikido_scraper.py  ← Aikido Security blog
    twis_scraper.py    ← This Week in Security
    anthropic_red_scraper.py ← Anthropic Red Team
  delivery/
    formatter.py       ← Converts scored items → markdown (title, body, tags); _pretty_source() slug map
    dispatch.py        ← Routes (title, body, tags) to delivery.method; always writes the markdown backup
    bear.py            ← Bear Notes via x-callback-url
    slack.py           ← Slack chat.postMessage (native message + threaded overflow)
    slack_format.py    ← Converts briefing markdown → Slack Block Kit groups
    backup.py          ← Markdown backup to ~/cyberbriefing-output/, 10-day retention (read by the weekly pipeline)
  db/
    state.py           ← SQLite at ~/.cyberbriefing/state.db
  weekly/              ← Weekly-summary package (reader, summariser, formatter, prompt.txt)
config.yaml            ← Source URLs, scoring weights, thresholds (edit me) — repo root, NOT in the package
config.local.yaml      ← Per-machine overrides, gitignored — repo root
install_launchd.sh     ← Generates the real plists from templates and bootstraps them
healthcheck.sh         ← Pre-flight check of every condition that has broken a morning fire
tests/                 ← Test suite — repo root
```

> **Paths in this document are package-relative.** A name like `delivery/dispatch.py` means `src/cyberbriefing/delivery/dispatch.py`. `config.yaml`, `config.local.yaml`, `.env` and `tests/` stay at the repo root — `config_loader.py` resolves the config files there via `Path(__file__).parents[2]`.

## Pipeline flow

1. **Gather**: APIs, then RSS feeds (8 threads), then interval-gated scrapers; items filtered against `state.db`. Failures are per-source and logged, never fatal.
2. **Score**: up to `max_score_input` (150) most-recent unseen items go to Claude in **50-item chunks** — independent calls, system prompt cached across them. Each chunk returns scored items tagged with a `cluster_id`. The response is constrained by `output_config.format` JSON schema (`scorer.RESPONSE_SCHEMA`), not by asking the prompt nicely — before that, a single missing closing brace could discard a whole chunk. Note the schema subset: every object needs `required` + `additionalProperties: false`, and numeric bounds are unsupported, so the 1–5 range lives in `prompt.txt`.
3. **Reconcile clusters** (only when >1 chunk): chunks are scored independently, so the same story in two chunks gets two `cluster_id` slugs `clusterer.py` can't merge. `deduplicator.reconcile_cluster_ids` makes one extra schema-constrained Claude call over *all* scored items to assign canonical ids. Strictly best-effort — any failure (API error, bad JSON, `max_tokens` truncation) leaves the per-chunk ids untouched and never empties the briefing. Output budget scales with item count (`deduplicator._output_budget`).
4. **Cluster**: items sharing a `cluster_id` are collapsed (highest score wins).
5. **Format**: Vulnerabilities (capped at `max_vuln_items`) leads, then Critical / Notable / Radar / Britain.
6. **Deliver**: via `delivery.method` — Bear or Slack (real run) or stdout (`--dry-run`); a markdown backup is always written except for `stdout`.
7. **Mark seen**: all gathered items written to `state.db` — **unless every chunk failed end-to-end** (`scoring_failed`, typically HTTP 529 across all retries). Then items are deliberately left unseen so the fallback fire retries the same set rather than only the trickle that arrived since; past 07:00 the failure is escalated to the delivery target instead of failing silently.

## Tiers and scoring

| Tier | Composite score | Render style |
|------|----------------|--------------|
| Critical | ≥ 17 | Full: heading, source, annotation, score |
| Notable | ≥ 13 | Full |
| Radar | ≥ 10 | Full |
| Britain | < 10 but geographic ≥ 4 | Headline-only bullet list |
| Excluded | < 10, not UK/EU | Not shown |

`composite = (geographic × 1.0) + (domain × 1.5) + (actionability × 1.2) + (novelty × 0.8)`, max 23.5. Geographic scores up to 6 (UK-specific items get an extra point); the other three are 1–5. Weights are tuned for UK-based appsec work.

**The Register** has nuanced guidance in `prompt.txt`: genuine appsec findings or UK breaches score normally; opinion/commentary gets low actionability and usually lands in Britain; stories already covered by a more technical source cluster under that primary; unique UK context nobody else covers gets promoted.

## Configuration

`config_loader.load_config()` reads `config.yaml` and deep-merges an optional, gitignored `config.local.yaml` over it (a nested override like `delivery.method` replaces just that leaf, leaving the rest of the `delivery` block intact). Both entry points load config this way; a machine with no local file uses the committed defaults. Everything host-specific — delivery method, scoring model, real Slack channel, launchd paths/schedule — lives in gitignored files copied from committed `*.example` templates, so the repo stays forkable.

`config.yaml` ships only a placeholder Slack channel (`C0XXXXXXXXX`), so a machine delivering to Slack **must** set the real `delivery.slack.channel` in `config.local.yaml`.

### Adding or removing sources

- **RSS feeds**: add under `sources.rss_feeds` with `url`, `category`, `source_name`. Note there is **no `enabled` flag for RSS** — every entry is fetched; remove or comment out an entry to drop it.
- **Scrapers**: add under `sources.scrapers` with `check_interval_hours`, *and* register the module in `briefing._SCRAPER_REGISTRY` — a config entry with no registry entry never runs, however complete it looks.
- **APIs**: each has its own collector module and an `enabled` flag under `sources.<name>`.
- Add the source slug → display name mapping in `delivery/formatter.py` `_pretty_source()`.

### Key tuning levers

| What | Where |
|------|-------|
| Scoring weights | `config.yaml` → `scoring.weights` |
| Score thresholds (tiers) | `config.yaml` → `scoring.threshold` + `prompt.txt` tier definitions |
| Always-include floor | `config.yaml` → `scoring.high_score_floor` (18; items at or above always survive the `max_items` cut) |
| Max items in briefing | `config.yaml` → `scoring.max_items` |
| Max items in the Vulnerabilities section | `config.yaml` → `scoring.max_vuln_items` (3; highest-scoring kept) |
| Max items sent to Claude | `config.yaml` → `scoring.max_score_input` |
| Scoring rubric / source guidance | `prioritiser/prompt.txt` |
| Section headers / render style | `delivery/formatter.py` |
| Scoring model | `config.yaml` → `scoring.model` (per-machine override in `config.local.yaml`) |
| Delivery target (bear / slack / stdout / markdown_file) | `config.yaml` → `delivery.method` (+ `delivery.slack.channel`) |

## Delivery

Both pipelines route through `delivery/dispatch.py`.

- **Backup invariant:** `dispatch.py` always writes the `~/cyberbriefing-output/` markdown backup for every method except `stdout`, because `weekly/reader.py` reads those backups. Bear/Slack posting is best-effort; **the backup is the durable artifact and the success signal.** Retention is 10 days (not 7) so a slightly-late run or a DST shift never prunes Monday's file before Sunday reads it.
- **Bear:** `open bear://x-callback-url/...` returns exit 0 the moment macOS accepts the URL handoff — there is no signal that Bear consumed it, so Bear delivery can never be confirmed client-side. When `pgrep` says Bear isn't running, `_launch_bear_and_wait()` does `open -ga Bear` and polls until Bear has been alive ≥2s (cap 15s) to clear the cold-launch race. **Bear has no AppleScript interface** (no `.sdef`, `sdef /Applications/Bear.app` → error -192) — don't reintroduce a `tell application "Bear"` fallback; it never worked.
- **Slack:** `SLACK_BOT_TOKEN` in env; only the `chat:write` scope, and the bot must be invited to the channel. `slack_format.py` remaps emphasis — Slack's `*bold*`/`_italic_` is the inverse of our markdown's `*italic*`. Long briefings overflow into threaded replies under the parent message.

## Scheduling

Cron-style launchd: a fresh process per calendar slot, no long-running daemon. Each archetype schedules a primary fire plus a later idempotent fallback — `was_delivered_today()` makes the fallback a free no-op on good days and the only thing that runs on bad ones. Two archetypes ship as templates; **state which one you mean before applying any scheduling or network reasoning:**

| Archetype | Templates | Schedule | pmset wake |
|-----------|-----------|----------|------------|
| Always-on desktop | `com.cyberbriefing.{daily,weekly}.plist.example` | Daily 06:15 + 07:30 fallback (Mon–Fri); weekly Sun 12:00 + 13:30 | Yes — required, see below |
| Sleeping laptop | `com.cyberbriefing.{daily,weekly}.laptop.plist.example` | Daily 08:40 (Mon–Fri); weekly Mon 10:00 | No — a closed lid can't be woken; launchd runs the missed job on next wake |

Both plists are hardened for correct user GUI context, which is load-bearing for DNS (see *Operational constraints* below): `LimitLoadToSessionType = Aqua`, `ProcessType = Interactive`, `RunAtLoad = false`, wrapped in `caffeinate -is`.

```bash
./install_launchd.sh              # both agents, always-on desktop archetype
./install_launchd.sh --laptop     # sleeping-laptop archetype
./install_launchd.sh --daily      # one agent only
./healthcheck.sh                  # pre-flight: plist context, pmset, secrets, recent output

# Always-on desktop only: a real user-session wake before the primary fire
sudo pmset repeat wakeorpoweron MTWRF 06:10:00   # verify: pmset -g sched

launchctl kickstart -k gui/$(id -u)/com.cyberbriefing.daily   # manual fire
launchctl print gui/$(id -u)/com.cyberbriefing.daily          # confirm Aqua / interactive (4)
tail -f /tmp/cyberbriefing.log /tmp/cyberbriefing.err
```

The install script generates the real (gitignored) plists from the templates by filling in `__PROJECT_DIR__` / `__USER__`, backs up any existing plist, and uses `bootout`/`bootstrap` rather than the deprecated `launchctl load` — only that pair reliably yields the interactive (4) spawn type.

## Weekly summary 🗓️

Rolls the week's daily briefings into one summary — `Weekly Cyber Summary — <Mon> to <Sun>`, tag `security/briefing/weekly`. It reads the daily markdown backups in `~/cyberbriefing-output/`, drops the Vulnerabilities (CVE) section, and asks Claude to dedupe/rank/summarise — biased towards blogs, tools and new techniques — into the top ~8–12 stories.

`weekly/reader.py: select_week_files` always targets the **most recently completed Mon→Sun week**, so a Sunday run and a Monday run summarise the same (just-ended) week, not the empty week starting today.

```bash
uv run cyberbriefing-weekly --dry-run   # → stdout, no state changes
uv run cyberbriefing-weekly             # → Bear or Slack (per delivery.method)
```

- **Code:** `weekly_run.py` + the `weekly/` package; reuses `delivery/dispatch.py` and `db/state.py`. Idempotency via `was_weekly_delivered_this_week()`.
- **Logs:** `/tmp/cyberbriefing-weekly.{log,err}`.
- **Failure:** empty week or Claude failure → `FAILURE-weekly-<date>.md` + non-zero exit; the fallback slot retries.

## Operational constraints (learned the hard way)

Four failure modes cost real debugging time. The fixes are in the code; the reasoning is here so it isn't re-derived or undone.

**1. Dark-wake DNS (EBADF).** On an always-on Mac, macOS holds the user session in a reduced "dark wake" overnight; `mDNSResponder`'s mach port is gated, and `getaddrinfo` returns `OSError: [Errno 9] Bad file descriptor` on every lookup — every collector fails. Two things are required together: the Aqua/Interactive plist context (a daemon-style spawn has no usable resolver port at all), **and** a `pmset repeat` wake a few minutes before the fire. `caffeinate -is` only blocks *new* sleep transitions during the run; it cannot restore a session already degraded at start. Ruled out and not worth re-exploring: DNS cache flush, `mDNSResponder` HUP, forcing `AF_INET`, respawning for fresh FDs, longer post-restart probe windows.

**2. All-sources-failed alarm.** `gather_all()` returns `(new_items, total_gathered)`. A healthy run gathers hundreds, so `total_gathered == 0` means a network-layer block, not a quiet news day. `run_pipeline()` writes `FAILURE-<YYYY-MM-DD>.md` to `~/cyberbriefing-output/` and exits non-zero so launchd records it — otherwise a blocked morning is indistinguishable from a slow one.

**3. Env-load hang.** A `.env` supplied as a 1Password local-env FIFO blocks in `open()` until 1Password attaches a writer. With 1Password locked at an unattended fire that blocked *forever* — no exception, no logs, no backup, slot held for an hour. `config_loader.load_env_with_timeout()` bounds each load with `SIGALRM` (EINTR, no leaked fd, so the single-reader FIFO stays retryable) and retries `30s × 2`; a plain regular-file `.env` returns instantly and is unaffected. The call lives in `main()` **after logging is configured**, not at import time — keep it there, or failures go invisible again and the test suite inherits the FIFO's behaviour.

**4. Whole-process watchdog.** `config_loader.arm_runtime_watchdog()` — a daemon-thread 15-minute process timeout armed at the top of `main()` in both entry points, so any future hang can't hold a launchd slot. A thread, not `SIGALRM`, so it never collides with (3). Caveat: the Anthropic client's 600s read timeout plus a half-size retry means a badly degraded API could approach 15 min and be killed mid-run — the fallback fire then retries.

Related fail-fast: `briefing._secrets_blocked()` aborts a real delivery run whose env load timed out *before* gather, writing a `secrets_unavailable` marker (accurate cause, not a misleading "scoring failed"). `--stats`, `--gather-only` and `--dry-run` are exempt.

## Secrets

A gitignored `.env` at the repo root, loaded via `load_env_with_timeout`. Either a plain file or a 1Password local-env FIFO works — see constraint 3 above for what the FIFO costs. Keys (names as the code reads them):

- `ANTHROPIC_API_KEY` — required, for Claude scoring
- `HACKERONE_API_USER` / `HACKERONE_API_TOKEN` — optional, HackerOne collector
- `NVD_API_KEY` — optional, higher NVD rate limits
- `GITHUB_TOKEN` — optional, GitHub Advisories collector
- `SLACK_BOT_TOKEN` — optional, only for `delivery.method: slack`

Missing optional keys degrade gracefully: the source is skipped with a warning.

## State DB

SQLite at `~/.cyberbriefing/state.db`:
- `seen_items` — every gathered item (id = SHA-256 of URL), tracks `included_in_briefing`. Auto-pruned monthly of never-included items older than 180 days (`prune_old_unseen`).
- `scraper_runs` — last-checked timestamp per scraper; also carries the daily/weekly delivered markers.

## Known gaps

Open ideas, none of them urgent — the pipeline has run without them for months. Listed so they aren't rediscovered as bugs.

- **No retry on collector HTTP calls.** Every collector sets a timeout but makes a single attempt; a transient failure just drops that source for the run. A `requests.Session` with a retry adapter would cover it.
- **No inter-request sleep in the NVD collector.** Without `NVD_API_KEY` the API allows 5 requests per 30s; `nvd.py` makes 2 (HIGH + CRITICAL) back to back, which is under the limit but has no margin if a third query is ever added.
- **No per-scraper zero-item warning.** The all-sources-failed alarm catches a total blackout, but a single scraper silently returning nothing after a site redesign looks identical to a quiet week for that source.
- **No `--log-file` flag.** launchd captures stdout to `/tmp/cyberbriefing.log`; a manual run has to be redirected by hand.

## Common issues

- **Empty briefing**: `--stats` for item counts; `--clear-source <slug>` to reset seen-state for one source and make it re-gather.
- **Bear note missing**: expected on some mornings and undetectable client-side — the markdown backup in `~/cyberbriefing-output/` is always written; open that.
- **`FAILURE-<date>.md`**: every source returned zero items — see constraint 2; check `/tmp/cyberbriefing.err` and any Network Extension (TripMode, Little Snitch, VPN).
- **`FAILURE-…-secrets_unavailable`**: the `.env` load timed out — unlock the secrets manager, or check the `.env` is readable.
- **A scraper returning zero items**: the site was probably redesigned; check that scraper's HTML selectors.
