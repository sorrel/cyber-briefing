# Cyber Briefing Tool

A cybersecurity intelligence briefing that can be delivered to Bear Notes or a Slack channel. The shipped scoring defaults are tuned for UK-based application security work, but the weights and the prompt are yours to retune.

## What it can do

Each run is a three-stage pipeline. You choose when (or whether) it runs — see [Scheduling](#scheduling-with-launchd):

1. **Gather** — Pulls from 25+ sources: CISA KEV, NVD, HackerOne, GitHub Advisories, NCSC, The Hacker News, PortSwigger, Krebs on Security, BleepingComputer, ENISA, ICO, UK Parliament, AWS Security, Wiz, Snyk, OWASP, Risky Business, TLDR Infosec, Aikido, CloudSecList, FeistyDuck, This Week in Security, and more. Any source can be disabled, and new RSS feeds are a config entry away.
2. **Prioritise** — Sends items to the Claude API for scoring across four dimensions (geographic relevance, domain relevance, actionability, novelty). The weights, the thresholds and the model are all configurable.
3. **Deliver** — Writes the prioritised items to Bear Notes or a Slack channel, grouped by urgency tier, with links and short annotations. How many items appear is set by `scoring.max_items` (ships as 20).

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

A companion pipeline can roll a week of briefings into a single **Weekly Cyber Summary** 📝, delivered to Bear or Slack like the daily one. It reads the daily markdown backups, skips the raw CVE list, and asks Claude to dedupe, rank and summarise the week's best reads — blogs, tools and new techniques — down to the top ~8–12 stories.

It always targets the **most recently completed Monday–Sunday week**, so it produces the same summary whether you schedule it for Sunday evening, Monday morning, or run it by hand. Run it as often or as rarely as suits you; it needs the daily backups in `~/cyberbriefing-output/` to have something to read.

```bash
uv run python weekly_run.py --dry-run   # preview to terminal
uv run python weekly_run.py             # real run → Bear or Slack (per config)
```

## Scheduling with launchd

Nothing here schedules itself — `briefing.py` is a one-shot script, and launchd is simply the recommended way to fire it unattended on macOS. The design is **cron-style**: launchd spawns a fresh process at each calendar slot, and there is no long-running daemon. Pairing a primary slot with a later fallback slot is worth doing, because a run no-ops cleanly if the day was already delivered, making the fallback free on good days and the safety net on bad ones.

The plists carry per-machine values (absolute path, username, schedule), so the **real `*.plist` files are gitignored** — only generic `*.plist.example` templates are committed. Two archetypes ship as starting points. **Their times are examples, not requirements** — edit the `StartCalendarInterval` block to whatever suits you:

| Template | For | Example schedule | pmset wake |
|----------|-----|------------------|------------|
| `com.cyberbriefing.daily.plist.example` + `com.cyberbriefing.weekly.plist.example` | An **always-on desktop** (a Mac left powered on) | Daily 06:15 (Mon–Fri) + weekly Sun 12:00 | Yes — see below |
| `com.cyberbriefing.daily.laptop.plist.example` + `com.cyberbriefing.weekly.laptop.plist.example` | A **laptop that sleeps** | Daily 08:40 (weekdays) + weekly Mon 10:00 | No — runs the missed job on next wake |

The archetypes differ in more than the clock. The desktop pair assumes the machine is awake and adds a `pmset` wake; the laptop pair assumes it is not, and leans on launchd running a missed calendar job at the next wake. Pick the one matching your machine's habits, then set the times to suit.

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

On an **always-on desktop**, also schedule a real user-session wake a few minutes before your primary fire. An idle Mac drops into a "dark wake" where DNS can fail, which is enough to break every collector. Match the days and time to the schedule you chose, a few minutes ahead of it:

```bash
# Example, for a 06:15 Mon–Fri fire — adjust the days and time to your own:
sudo pmset repeat wakeorpoweron MTWRF 06:10:00   # verify with: pmset -g sched
```

Repeat steps 1–3 for the weekly template. Logs are at `/tmp/cyberbriefing.log` / `.err` (daily) and `/tmp/cyberbriefing-weekly.log` / `.err` (weekly). Test the pipeline any time without launchd via `uv run python briefing.py --dry-run`.

## Configuration

Edit `config.yaml` to:

- Enable/disable individual sources (`sources.*.enabled`)
- Adjust scoring weights and tier thresholds (`scoring.weights`, `scoring.threshold`)
- Add new RSS feeds (just add an entry under `sources.rss_feeds`)
- Change how many items reach the briefing (`scoring.max_items`)
- Choose the scoring model (`scoring.model`)
- Switch delivery method (`delivery.method`: bear, slack, stdout, or markdown_file)

Edit `prioritiser/prompt.txt` to tune the AI scoring. This is where you adjust priorities without touching code.

### Per-machine overrides (`config.local.yaml`)

`config.yaml` holds the shared defaults. Anything specific to one machine —
delivery method, scoring model, your real Slack channel — goes in a gitignored
`config.local.yaml` that is deep-merged over `config.yaml`. Copy the template
to get started:

```bash
cp config.local.yaml.example config.local.yaml
```

This lets one repo drive different delivery targets, scoring models or schedules
on different machines without diverging the committed files — a desktop
delivering to Bear and a laptop posting to Slack can share the same clone.

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

## Costs

The only running cost is Claude API usage for scoring, so it scales with how often you run it, how many sources you enable, and which model you pick. As a rough anchor: Sonnet scoring ~50–100 items a day works out around 2–4 GBP per month. Levers if you want it cheaper:

- **`scoring.model`** — a Haiku-class model is substantially cheaper per token than a Sonnet-class one, at some cost in ranking nuance.
- **`scoring.max_score_input`** — caps how many items are sent for scoring at all.
- **Run it less often**, or disable the noisier sources in `config.yaml`.
