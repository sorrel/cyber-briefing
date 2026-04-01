# Cyber Briefing Tool — Claude Code Context

## What this is

A Python pipeline that runs daily to produce a prioritised cybersecurity briefing, delivered to Bear Notes. It gathers from 17+ sources (APIs, RSS feeds, scrapers), scores each item using Claude, and formats a tiered markdown document.

## Running it

```bash
# Always use uv
uv run python briefing.py --dry-run     # Full pipeline → stdout
uv run python briefing.py --gather-only # Collect only, mark seen, no scoring
uv run python briefing.py --stats       # Show DB stats by source
uv run python briefing.py               # Real run → Bear Notes
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

launchd plist: `com.cyberbriefing.daily.plist` fires daily at 06:00.

```bash
# Install
cp com.cyberbriefing.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Logs
tail -f /tmp/cyberbriefing.log
tail -f /tmp/cyberbriefing.err
```

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
