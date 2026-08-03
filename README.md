# Cyber Briefing Tool

A cybersecurity intelligence briefing that can be delivered to Bear Notes or a Slack channel. The shipped scoring defaults are tuned for UK-based application security work, but the weights and the prompt are yours to retune.

## What it can do

Each run is a three-stage pipeline. You choose when (or whether) it runs — see [Scheduling](#scheduling-with-launchd):

1. **Gather** — Pulls from 38 sources: CISA KEV, NVD, HackerOne, GitHub Advisories, NCSC, The Hacker News, PortSwigger, Krebs on Security, BleepingComputer, ENISA, ICO, AWS Security, Wiz, Snyk, OWASP, Trail of Bits, Project Zero, Risky Business, TLDR Infosec, Aikido, CloudSecList, FeistyDuck, This Week in Security, and more. New RSS feeds are a config entry away.
2. **Prioritise** — Sends items to the Claude API for scoring across four dimensions (geographic relevance, domain relevance, actionability, novelty). The weights, the thresholds and the model are all configurable.
3. **Deliver** — Writes the prioritised items to Bear Notes or a Slack channel, grouped by urgency tier, with links and short annotations. How many items appear is set by `scoring.max_items` (ships as 20).

## Quick start

```bash
cd /path/to/cyberbriefing

# Secrets
cp .env.example .env        # then fill in your API keys

# Dry run — full pipeline, prints to the terminal, delivers nothing
uv run cyberbriefing --dry-run

# Real run — delivers to Bear or Slack, per config
uv run cyberbriefing
```

This project uses [uv](https://github.com/astral-sh/uv) for dependency management. Run `uv sync` if you need to install dependencies explicitly.

## Weekly summary 🗓️

A companion pipeline rolls a week of briefings into a single **Weekly Cyber Summary** 📝, delivered to Bear or Slack like the daily one. It reads the daily markdown backups, skips the raw CVE list, and asks Claude to dedupe, rank and summarise the week's best reads — blogs, tools and new techniques — down to the top ~8–12 stories.

It always targets the **most recently completed Monday–Sunday week**, so it produces the same summary whether you schedule it for Sunday evening, Monday morning, or run it by hand. It needs the daily backups in `~/cyberbriefing-output/` to have something to read.

```bash
uv run cyberbriefing-weekly --dry-run   # preview to terminal
uv run cyberbriefing-weekly             # real run → Bear or Slack (per config)
```

## Scheduling with launchd

Nothing here schedules itself — `cyberbriefing` is a one-shot command, and launchd is simply the recommended way to fire it unattended on macOS. Each fire is a fresh process; there is no long-running daemon. Pairing a primary slot with a later fallback slot is worth doing: a run no-ops cleanly if the day was already delivered, so the fallback is free on good days and the safety net on bad ones.

The plists carry per-machine values (absolute path, username, schedule), so the **real `*.plist` files are gitignored** — only generic `*.plist.example` templates are committed. Two archetypes ship as starting points. **Their times are examples, not requirements** — edit the `StartCalendarInterval` block to whatever suits you:

| Template | For | Example schedule | pmset wake |
|----------|-----|------------------|------------|
| `com.cyberbriefing.{daily,weekly}.plist.example` | An **always-on desktop** | Daily 06:15 (Mon–Fri) + weekly Sun 12:00 | Yes — see below |
| `com.cyberbriefing.{daily,weekly}.laptop.plist.example` | A **laptop that sleeps** | Daily 08:40 (Mon–Fri) + weekly Mon 10:00 | No — runs the missed job on next wake |

`install_launchd.sh` does the whole install: it generates the real plists from the templates (filling in the path and username), backs up anything already there, and loads them into the GUI session.

```bash
./install_launchd.sh              # both agents, always-on desktop archetype
./install_launchd.sh --laptop     # sleeping-laptop archetype
./install_launchd.sh --daily      # just the daily agent
./healthcheck.sh                  # pre-flight check of everything that can break a fire
```

On an **always-on desktop**, also schedule a real user-session wake a few minutes before your primary fire. An idle Mac drops into a "dark wake" where DNS can fail, which is enough to break every collector:

```bash
# Example, for a 06:15 Mon–Fri fire — adjust the days and time to your own:
sudo pmset repeat wakeorpoweron MTWRF 06:10:00   # verify with: pmset -g sched
```

Logs are at `/tmp/cyberbriefing.log` / `.err` (daily) and `/tmp/cyberbriefing-weekly.log` / `.err` (weekly). Test any time without launchd via `uv run cyberbriefing --dry-run`.

## Configuration

Edit `config.yaml` to:

- Add RSS feeds (an entry under `sources.rss_feeds`; to drop one, remove or comment it out)
- Enable/disable the API and scraper sources (`sources.<name>.enabled`)
- Adjust scoring weights and tier thresholds (`scoring.weights`, `scoring.threshold`)
- Change how many items reach the briefing (`scoring.max_items`)
- Choose the scoring model (`scoring.model`)
- Switch delivery method (`delivery.method`: bear, slack, stdout, or markdown_file)

Edit `src/cyberbriefing/prioritiser/prompt.txt` to tune the AI scoring — that's where you adjust priorities without touching code.

### Per-machine overrides (`config.local.yaml`)

`config.yaml` holds the shared defaults. Anything specific to one machine — delivery method, scoring model, your real Slack channel — goes in a gitignored `config.local.yaml` that is deep-merged over it:

```bash
cp config.local.yaml.example config.local.yaml
```

One clone can then drive different delivery targets and models on different machines without diverging the committed files — a desktop delivering to Bear and a laptop posting to Slack can share the same repo.

## Slack delivery

Set `delivery.method: slack` (in `config.local.yaml`, or `config.yaml` if every machine uses Slack) to post the briefing to a Slack channel instead of Bear. Both the daily briefing and the weekly summary honour it; a dated markdown backup is still written to `~/cyberbriefing-output/` either way.

One-time setup:

1. Create a Slack app, add the **`chat:write`** bot scope, install it to your workspace.
2. `/invite` the bot into the target channel.
3. Put the channel ID under `delivery.slack.channel` in `config.local.yaml` (`config.yaml` ships a placeholder, not a real channel).
4. Provide `SLACK_BOT_TOKEN` in your `.env`.

The briefing posts as a native Slack message; anything past Slack's per-message limit continues in threaded replies.

## CLI options

| Flag | Description |
|------|-------------|
| `--dry-run` | Full pipeline, prints to stdout instead of delivering |
| `--gather-only` | Just gather items and show counts |
| `--stats` | Show database statistics by source |
| `--clear-source SOURCE` | Reset seen-state for one source so it re-gathers next run |
| `--verbose` / `-v` | Debug logging |

## API keys needed

| Key | Source | Required? |
|-----|--------|-----------|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Yes |
| `HACKERONE_API_USER` + `HACKERONE_API_TOKEN` | HackerOne Settings | Optional |
| `NVD_API_KEY` | nvd.nist.gov | Optional (higher rate limits) |
| `GITHUB_TOKEN` | GitHub Settings | Optional (for advisories) |
| `SLACK_BOT_TOKEN` | Slack app (chat:write) | Optional (only for `delivery.method: slack`) |

The tool degrades gracefully: if a key is missing, that source is skipped and logged as a warning.

## Costs

The only running cost is Claude API usage for scoring, so it scales with how often you run it, how many sources you enable, and which model you pick. As a rough anchor: Sonnet scoring ~50–100 items a day works out around 2–4 GBP per month. Levers if you want it cheaper:

- **`scoring.model`** — a Haiku-class model is substantially cheaper per token than a Sonnet-class one, at some cost in ranking nuance.
- **`scoring.max_score_input`** — caps how many items are sent for scoring at all.
- **Run it less often**, or drop the noisier sources from `config.yaml`.
