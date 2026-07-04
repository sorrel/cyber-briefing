# Cyber Briefing Tool

A daily cybersecurity intelligence briefing delivered to Bear Notes or a Slack channel, tailored for an application security professional based in the UK.

## What it does

Runs a three-stage pipeline every morning at 06:00:

1. **Gather** — Pulls from 25+ sources: CISA KEV, NVD, HackerOne, GitHub Advisories, NCSC, The Hacker News, PortSwigger, Krebs on Security, BleepingComputer, ENISA, ICO, UK Parliament, AWS Security, Wiz, Snyk, OWASP, Risky Business, TLDR Infosec, Aikido, CloudSecList, FeistyDuck, This Week in Security, and more.
2. **Prioritise** — Sends items to the Claude API for scoring across four dimensions (geographic relevance, domain relevance, actionability, novelty) with weights tuned for UK-based appsec work.
3. **Deliver** — Delivers up to 20 prioritised items to Bear Notes or a Slack channel (configurable), grouped by urgency tier, with links and short annotations.

## Quick start

```bash
# 1. Move into your scripts directory
cd /path/to/cyberbriefing

# 2. Set up secrets
cp .env.example .env
# Edit .env with your actual API keys

# 3. Test with a dry run (prints to terminal instead of delivering)
uv run python briefing.py --dry-run

# 4. Test just the gathering stage
uv run python briefing.py --gather-only

# 5. Full run (delivers to Bear or Slack, per config)
uv run python briefing.py
```

This project uses [uv](https://github.com/astral-sh/uv) for dependency management. Run `uv sync` if you need to install dependencies explicitly.

## Weekly summary 🗓️

Alongside the daily briefing, a companion job runs every **Sunday at midday** and rolls the week's briefings into a single **Weekly Cyber Summary** Bear note 📝. It reads the daily backups, skips the raw CVE list, and asks Claude to dedupe, rank and summarise the week's best reads — blogs, tools and new techniques — down to the top ~8–12 stories.

```bash
uv run python weekly_run.py --dry-run   # preview to terminal
uv run python weekly_run.py             # real run → Bear or Slack (per config)
```

## Scheduling with launchd

The briefing runs **cron-style**: launchd spawns a fresh `briefing.py` process at each calendar slot (a primary fire plus a ~1h idempotent fallback that no-ops if the day was already delivered). There is no long-running daemon.

The launchd plists carry per-machine values (absolute path, username, schedule), so the **real `*.plist` files are gitignored** — only generic `*.plist.example` templates are committed. Two archetypes are provided:

| Template | For | Schedule | pmset wake |
|----------|-----|----------|------------|
| `com.cyberbriefing.daily.plist.example` + `com.cyberbriefing.weekly.plist.example` | An **always-on desktop** (a Mac left powered on) | Daily 06:15 (Mon–Fri) + weekly Sun 12:00 | Yes — needed |
| `com.cyberbriefing.daily.laptop.plist.example` + `com.cyberbriefing.weekly.laptop.plist.example` | A **laptop that sleeps** | Daily 08:40 (weekdays) + weekly Mon 10:00 | No — runs the missed job on next wake |

Install (using the always-on desktop pair as the example):

```bash
# 1. Copy the template to the real (gitignored) filename
cp com.cyberbriefing.daily.plist.example com.cyberbriefing.daily.plist

# 2. Fill in the two placeholders in the copy:
#    __PROJECT_DIR__  → absolute path to this repo (e.g. /Users/you/cyberbriefing)
#    __USER__         → your macOS short username

# 3. Copy into LaunchAgents and load it
cp com.cyberbriefing.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Confirm it loaded in the Aqua GUI session (needed for DNS to work at run time)
launchctl print gui/$(id -u)/com.cyberbriefing.daily

# Reload after edits
launchctl bootout gui/$(id -u)/com.cyberbriefing.daily
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cyberbriefing.daily.plist
```

On an **always-on desktop**, also schedule a real user-session wake a few minutes before the primary fire (an idle Mac drops into a "dark wake" where DNS can fail):

```bash
sudo pmset repeat wakeorpoweron MTWRF 06:10:00   # verify with: pmset -g sched
```

Repeat steps 1–3 for the weekly template. Logs are at `/tmp/cyberbriefing.log` / `.err` (daily) and `/tmp/cyberbriefing-weekly.log` / `.err` (weekly). Test the pipeline any time without launchd via `uv run python briefing.py --dry-run`.

## Configuration

Edit `config.yaml` to:

- Enable/disable individual sources
- Adjust scoring weights and thresholds
- Add new RSS feeds (just add an entry under `sources.rss_feeds`)
- Change the target number of items per briefing
- Switch delivery method (bear, slack, stdout, or markdown_file)

Edit `prioritiser/prompt.txt` to tune the AI scoring. This is where you adjust priorities without touching code.

### Per-machine overrides (`config.local.yaml`)

`config.yaml` holds the shared defaults. Anything specific to one machine —
delivery method, scoring model, your real Slack channel — goes in a gitignored
`config.local.yaml` that is deep-merged over `config.yaml`. Copy the template
to get started:

```bash
cp config.local.yaml.example config.local.yaml
```

This lets one repo drive different delivery targets or schedules on different
machines without diverging the committed files.

## Slack delivery

Set `delivery.method: slack` (in `config.local.yaml`, or `config.yaml` if every
machine uses Slack) to post the briefing to a Slack channel instead of Bear.
Both the daily briefing and the weekly summary honour this setting; a dated
markdown backup is still written to `~/cyberbriefing-output/` regardless.

One-time setup:

1. Create a Slack app, add the **`chat:write`** bot scope, and install it to
   your workspace.
2. `/invite` the bot into the target channel.
3. Put the channel ID under `delivery.slack.channel` in `config.local.yaml`
   (`config.yaml` ships a placeholder channel ID, not a real one).
4. Provide `SLACK_BOT_TOKEN` via your `.env` (see below).

The briefing is rendered as a native Slack message; anything longer than
Slack's per-message limit is posted as threaded replies under it.

## CLI options

| Flag | Description |
|------|-------------|
| `--dry-run` | Full pipeline, prints to stdout instead of delivering |
| `--gather-only` | Just gather items and show counts |
| `--stats` | Show database statistics |
| `--verbose` / `-v` | Debug logging |

## API keys needed

| Key | Source | Required? |
|-----|--------|-----------|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Yes |
| `HACKERONE_USERNAME` + `HACKERONE_TOKEN` | HackerOne Settings | Optional |
| `NVD_API_KEY` | nvd.nist.gov | Optional (higher rate limits) |
| `GITHUB_TOKEN` | GitHub Settings | Optional (for advisories) |
| `SLACK_BOT_TOKEN` | Slack app (chat:write) | Optional (only for `delivery.method: slack`) |

The tool degrades gracefully. If a key is missing, that source is skipped and logged as a warning.

## Estimated costs

With Claude Sonnet scoring ~50–100 items daily: roughly 2–4 GBP per month in API usage.
