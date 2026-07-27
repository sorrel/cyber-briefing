# Cyber Briefing Tool — Handoff Notes for Claude Code

## Current status

**Built and tested.** All modules import cleanly, unit tests pass, live RSS and CISA KEV collectors confirmed working against real feeds. One git commit on `main` branch.

## Setup on this machine

```bash
# Move the project into place
mv cyberbriefing /path/to/your/scripts/

# Set up with uv (creates the venv and installs from uv.lock)
cd /path/to/your/scripts/cyberbriefing
uv sync

# Set up secrets
cp .env.example .env
# Edit .env with real API keys (or use 1Password CLI)

# Quick test
uv run cyberbriefing --gather-only
uv run cyberbriefing --dry-run
```

## What's been built

### Collectors (all working)
- `collectors/rss.py` — Generic RSS/Atom, handles 12 feeds with optional keyword filtering
- `collectors/cisa_kev.py` — CISA KEV JSON catalogue, returns all entries (dedup via state DB)
- `collectors/nvd.py` — NVD 2.0 API, 48h lookback, CVSS ≥ 7.0 + CRITICAL
- `collectors/hackerone.py` — HackerOne Hacktivity API (needs credentials)
- `collectors/github_advisories.py` — GitHub GraphQL (needs token)
- `collectors/enisa_scraper.py` — ENISA publications page scraper
- `collectors/ico_scraper.py` — ICO enforcement actions scraper

### Pipeline
- `db/state.py` — SQLite state DB (~/.cyberbriefing/state.db)
- `prioritiser/scorer.py` — Claude API scoring call
- `prioritiser/clusterer.py` — Story deduplication/clustering
- `prioritiser/prompt.txt` — Editable scoring system prompt
- `delivery/formatter.py` — Markdown formatter (returns title, body, tags tuple)
- `delivery/bear.py` — Bear Notes delivery (x-callback-url → AppleScript → markdown fallback)
- `briefing.py` — Main orchestrator with CLI (--dry-run, --gather-only, --stats)

### Config
- `config.yaml` — All sources, scoring weights, thresholds
- `pyproject.toml` + `uv.lock` — dependency manifest (uv only; no `requirements.txt`)
- `.env.example` — Template for secrets
- `com.cyberbriefing.daily.plist` — launchd scheduler template (run `install_launchd.sh` to install with correct paths)

## Known issues / remaining work

### Must fix
1. **The `__init__.py` files**: Git created a single file `{collectors,prioritiser,delivery,db}/__init__.py` instead of four separate files. Run:
   ```bash
   rm '{collectors,prioritiser,delivery,db}/__init__.py'
   touch collectors/__init__.py prioritiser/__init__.py delivery/__init__.py db/__init__.py
   git add -A && git commit -m "fix: create proper __init__.py files"
   ```

2. **End-to-end test with Claude API**: The scorer module hasn't been tested with a real API key. Run `uv run cyberbriefing --dry-run` with `ANTHROPIC_API_KEY` set to verify.

### Should do (Phase 4 polish)
3. **Error handling**: Add retry logic for transient network failures in collectors (requests.Session with retries adapter).
4. **Rate limiting**: NVD API without a key is rate-limited to 5 requests per 30 seconds. The collector makes 2 requests (HIGH + CRITICAL) which is fine, but add a small sleep between them.
5. **ENISA scraper fragility**: The scraper parses `a[href*='/publications/']` which may break if ENISA redesigns. Consider adding a health check that logs a warning if zero items found.
6. **Logging to file**: Currently logs to /tmp/cyberbriefing.log via launchd. Consider adding a `--log-file` option for when running manually.
7. **Bear URL length limits**: Very long briefings may exceed URL length limits for the x-callback-url method. The AppleScript fallback handles this, but could add an explicit check.

### Nice to have (future)
8. Weekly digest note summarising the week's briefings
9. Trend detection across multiple days
10. Scoring prompt feedback loop (thumbs up/down on items)
11. Bugcrowd collector
12. GCP Security Blog RSS feed

## Architecture notes

- `format_briefing()` returns `(title, body, tags_list)` — the formatter owns title generation and tag collection.
- `deliver_to_bear()` and `deliver_to_stdout()` both take `(title, body, tags_list)`.
- `briefing.py` uses `deliver_to_bear()` for real runs and `deliver_to_stdout()` for --dry-run.
- All collectors return items in the common schema defined in `collectors/base.py`.
- The state DB handles dedup — collectors can return all items and the orchestrator filters.
- Scrapers respect `check_interval_hours` from config via `should_check_scraper()`.

## File tree

```
cyberbriefing/
├── src/
│   └── cyberbriefing/         # the importable package (src layout)
│       ├── __init__.py
│       ├── briefing.py           # Entry point (console script: cyberbriefing)
│       ├── weekly_run.py         # Weekly entry point (cyberbriefing-weekly)
│       ├── config_loader.py      # loads config.yaml (+ config.local.yaml) and .env
│       ├── collectors/
│       │   ├── base.py           # Common schema
│       │   ├── rss.py            # Generic RSS
│       │   ├── cisa_kev.py       # CISA KEV API
│       │   ├── nvd.py            # NVD CVE API
│       │   ├── hackerone.py      # HackerOne API
│       │   ├── github_advisories.py  # GitHub GraphQL
│       │   ├── enisa_scraper.py      # ENISA web scraper
│       │   └── ico_scraper.py        # ICO web scraper
│       ├── prioritiser/
│       │   ├── scorer.py         # Claude API call
│       │   ├── clusterer.py      # Story grouping
│       │   └── prompt.txt        # Editable scoring prompt
│       ├── delivery/
│       │   ├── formatter.py      # Markdown output
│       │   ├── dispatch.py       # Routes to bear / slack / stdout
│       │   ├── bear.py           # Bear Notes delivery
│       │   └── slack.py          # Slack delivery
│       └── db/
│           └── state.py          # SQLite state tracking
├── config.yaml               # Source registry + scoring config (repo root)
├── config.local.yaml.example # Per-machine overrides template
├── pyproject.toml            # uv dependency manifest + console scripts
├── uv.lock                   # Pinned dependency lock (uv)
├── .env.example              # Secrets template
├── .gitignore
├── README.md
├── tests/                    # test suite (stays at repo root)
└── com.cyberbriefing.daily.plist.example  # launchd schedule template
```
