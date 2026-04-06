# Cyber Briefing Tool

A daily cybersecurity intelligence briefing delivered to Bear Notes, tailored for an application security professional based in the UK.

## What it does

Runs a three-stage pipeline every morning at 06:00:

1. **Gather** — Pulls from 25+ sources: CISA KEV, NVD, HackerOne, GitHub Advisories, NCSC, The Hacker News, PortSwigger, Krebs on Security, BleepingComputer, ENISA, ICO, UK Parliament, AWS Security, Wiz, Snyk, OWASP, Risky Business, TLDR Infosec, Aikido, CloudSecList, FeistyDuck, and more.
2. **Prioritise** — Sends items to the Claude API for scoring across four dimensions (geographic relevance, domain relevance, actionability, novelty) with weights tuned for UK-based appsec work.
3. **Deliver** — Creates a Bear Note with up to 20 prioritised items, grouped by urgency tier, with links and short annotations.

## Quick start

```bash
# 1. Move into your scripts directory
cd /path/to/cyberbriefing

# 2. Set up secrets
cp .env.example .env
# Edit .env with your actual API keys

# 3. Test with a dry run (prints to terminal instead of Bear)
uv run python briefing.py --dry-run

# 4. Test just the gathering stage
uv run python briefing.py --gather-only

# 5. Full run (creates a Bear note)
uv run python briefing.py
```

This project uses [uv](https://github.com/astral-sh/uv) for dependency management. Run `uv sync` if you need to install dependencies explicitly.

## Scheduling with launchd

```bash
# Copy the plist to LaunchAgents
cp com.cyberbriefing.daily.plist ~/Library/LaunchAgents/

# Load the schedule
launchctl load ~/Library/LaunchAgents/com.cyberbriefing.daily.plist

# Verify it is loaded
launchctl list | grep cyberbriefing

# To run it immediately (for testing)
launchctl start com.cyberbriefing.daily

# To unload
launchctl unload ~/Library/LaunchAgents/com.cyberbriefing.daily.plist
```

The job fires at 06:00 daily. If your Mac is asleep at that time, launchd will run the job when the machine next wakes.

Check logs at `/tmp/cyberbriefing.log` and `/tmp/cyberbriefing.err`.

## Configuration

Edit `config.yaml` to:

- Enable/disable individual sources
- Adjust scoring weights and thresholds
- Add new RSS feeds (just add an entry under `sources.rss_feeds`)
- Change the target number of items per briefing
- Switch delivery method (bear, stdout, or markdown_file)

Edit `prioritiser/prompt.txt` to tune the AI scoring. This is where you adjust priorities without touching code.

## CLI options

| Flag | Description |
|------|-------------|
| `--dry-run` | Full pipeline, prints to stdout instead of Bear |
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

The tool degrades gracefully. If a key is missing, that source is skipped and logged as a warning.

## Estimated costs

With Claude Sonnet scoring ~50–100 items daily: roughly 2–4 GBP per month in API usage.
