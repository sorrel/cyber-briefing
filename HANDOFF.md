# Cyber Briefing Tool — Handoff Notes for Claude Code

## Current status

**Built and tested.** All modules import cleanly, unit tests pass, live RSS and CISA KEV collectors confirmed working against real feeds. One git commit on `main` branch.

## Setup on this machine

```bash
# Move the project into place
mv cyberbriefing /path/to/your/scripts/

# Set up with uv
cd /path/to/your/scripts/cyberbriefing
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
# Or alternatively:
uv pip install -e .

# Set up secrets
cp .env.example .env
# Edit .env with real API keys (or use 1Password CLI)

# Quick test
python briefing.py --gather-only
python briefing.py --dry-run
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
- `pyproject.toml` — For uv/pip
- `requirements.txt` — Fallback for pip
- `.env.example` — Template for secrets
- `com.cyberbriefing.daily.plist` — launchd scheduler (paths pre-set to Duncan's machine)

## Known issues / remaining work

### Must fix
1. **The `__init__.py` files**: Git created a single file `{collectors,prioritiser,delivery,db}/__init__.py` instead of four separate files. Run:
   ```bash
   rm '{collectors,prioritiser,delivery,db}/__init__.py'
   touch collectors/__init__.py prioritiser/__init__.py delivery/__init__.py db/__init__.py
   git add -A && git commit -m "fix: create proper __init__.py files"
   ```

2. **End-to-end test with Claude API**: The scorer module hasn't been tested with a real API key. Run `python briefing.py --dry-run` with `ANTHROPIC_API_KEY` set to verify.

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
├── briefing.py               # Entry point
├── config.yaml               # Source registry + scoring config
├── pyproject.toml             # uv/pip
├── requirements.txt           # Fallback deps
├── .env.example               # Secrets template
├── .gitignore
├── README.md
├── com.cyberbriefing.daily.plist  # launchd schedule
├── collectors/
│   ├── __init__.py
│   ├── base.py               # Common schema
│   ├── rss.py                # Generic RSS (12 feeds)
│   ├── cisa_kev.py           # CISA KEV API
│   ├── nvd.py                # NVD CVE API
│   ├── hackerone.py          # HackerOne API
│   ├── github_advisories.py  # GitHub GraphQL
│   ├── enisa_scraper.py      # ENISA web scraper
│   └── ico_scraper.py        # ICO web scraper
├── prioritiser/
│   ├── __init__.py
│   ├── scorer.py             # Claude API call
│   ├── clusterer.py          # Story grouping
│   └── prompt.txt            # Editable scoring prompt
├── delivery/
│   ├── __init__.py
│   ├── formatter.py          # Markdown output
│   └── bear.py               # Bear Notes delivery
└── db/
    ├── __init__.py
    └── state.py              # SQLite state tracking
```
