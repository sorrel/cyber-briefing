# Move source into a `src/cyberbriefing/` package — design

- **Date:** 2026-07-27
- **Status:** approved (design), pending implementation plan
- **Type:** structural refactor (no behaviour change)

## Goal

Adopt the conventional Python **src layout** so this repo matches the house
pattern used across our other programs: one importable package living in
`src/<package>/`. This is *not* about publishing a distributable — nothing goes
to PyPI and no wheel is shipped. It is about a consistent, conventional project
shape.

## Approach (chosen)

**Named package under `src/`** — a single package `src/cyberbriefing/` holding
all current source. Imports become absolute `cyberbriefing.*`. The project gains
a build backend so `uv` installs it (editable) into its own venv, which is what
makes `import cyberbriefing.*` and the console scripts resolve. This is a local
editable install for development/run — **not** shipping an installable artifact.

Rejected alternative: a "bare `src/` source-root" holding the existing loose
modules with imports unchanged. Lower churn, but it is not the recognised src
layout convention, which was the whole point of the exercise.

### Decisions made during design

- **Absolute imports** (`from cyberbriefing.db import state`), not relative —
  the conventional, readable choice for cross-subpackage imports (PEP 8).
- **A second console script `cyberbriefing-weekly`** for the weekly pipeline,
  symmetric with the daily `cyberbriefing`. `weekly_run.py` already has `main()`.
- **`config.yaml`, `config.local.yaml`, `.env` stay at the repo root** (they are
  edited-in-place / per-machine app config, not package data). Their `__file__`
  anchors move from `.parent` to `.parents[2]` (repo root from a top-level
  package module).
- **`tests/` stays at the repo root** (conventional; tests are not part of the
  shipped package).

## Target layout

```
cyber-briefing/                        ← repo root
├── src/
│   └── cyberbriefing/                 ← the one importable package
│       ├── __init__.py                ← NEW (package marker)
│       ├── briefing.py                ← git mv from ./briefing.py     (main())
│       ├── weekly_run.py              ← git mv from ./weekly_run.py   (main())
│       ├── config_loader.py           ← git mv from ./config_loader.py
│       ├── collectors/                ← git mv (keeps its __init__.py + all modules)
│       ├── db/                        ← git mv (keeps __init__.py, state.py)
│       ├── delivery/                  ← git mv (keeps __init__.py + all modules)
│       ├── prioritiser/               ← git mv (keeps __init__.py, *.py, prompt.txt, dedup_prompt.txt)
│       └── weekly/                    ← git mv (keeps __init__.py, *.py, prompt.txt)
├── config.yaml                        ← STAYS (committed defaults; "edit me")
├── config.local.yaml.example          ← STAYS (per-machine template)
├── .env.example                       ← STAYS (per-machine template)
├── tests/                             ← STAYS (import lines updated)
├── pyproject.toml                     ← build-system + scripts updated
├── uv.lock  README.md  CLAUDE.md  HANDOFF.md
├── com.cyberbriefing.*.plist.example  ← invocation updated
├── install_launchd.sh  healthcheck.sh
└── .gitignore  .python-version
```

All moves use `git mv` to preserve history.

## File moves

`git mv` these into `src/cyberbriefing/` (directories move whole, keeping their
existing `__init__.py` and their `.txt` prompt files):

- `briefing.py`, `weekly_run.py`, `config_loader.py`
- `collectors/`, `db/`, `delivery/`, `prioritiser/`, `weekly/`

New file: `src/cyberbriefing/__init__.py` — minimal (module docstring only; no
re-exports needed).

## Import rewrites

Prefix every import of a moved top-level name with `cyberbriefing.`. The moved
top-level names are: `config_loader`, `collectors`, `db`, `delivery`,
`prioritiser`, `weekly`, `briefing`, `weekly_run`.

### Source (7 files)

| File | Before | After |
|---|---|---|
| `briefing.py` | `import config_loader` | `from cyberbriefing import config_loader` |
| `briefing.py` | `from collectors import rss, cisa_kev, …` | `from cyberbriefing.collectors import rss, cisa_kev, …` |
| `briefing.py` | `from collectors import enisa_scraper, …` | `from cyberbriefing.collectors import enisa_scraper, …` |
| `briefing.py` | `from db.state import …` | `from cyberbriefing.db.state import …` |
| `briefing.py` | `from prioritiser.scorer import score_items` | `from cyberbriefing.prioritiser.scorer import score_items` |
| `briefing.py` | `from prioritiser.clusterer import cluster_items` | `from cyberbriefing.prioritiser.clusterer import cluster_items` |
| `briefing.py` | `from delivery.formatter import …` | `from cyberbriefing.delivery.formatter import …` |
| `briefing.py` | `from delivery.bear import deliver_to_stdout` | `from cyberbriefing.delivery.bear import deliver_to_stdout` |
| `briefing.py` | `from delivery.dispatch import deliver` | `from cyberbriefing.delivery.dispatch import deliver` |
| `weekly_run.py` | `import config_loader` | `from cyberbriefing import config_loader` |
| `weekly_run.py` | `from db import state` | `from cyberbriefing.db import state` |
| `weekly_run.py` | `from delivery.bear import deliver_to_stdout` | `from cyberbriefing.delivery.bear import deliver_to_stdout` |
| `weekly_run.py` | `from delivery.dispatch import deliver` | `from cyberbriefing.delivery.dispatch import deliver` |
| `weekly_run.py` | `from weekly.formatter import format_weekly` | `from cyberbriefing.weekly.formatter import format_weekly` |
| `weekly_run.py` | `from weekly.reader import read_week` | `from cyberbriefing.weekly.reader import read_week` |
| `weekly_run.py` | `from weekly.summariser import summarise_week` | `from cyberbriefing.weekly.summariser import summarise_week` |
| `delivery/dispatch.py` | `from delivery.backup import write_markdown_backup` | `from cyberbriefing.delivery.backup import write_markdown_backup` |
| `delivery/dispatch.py` | `from delivery.bear import deliver_to_bear, deliver_to_stdout` | `from cyberbriefing.delivery.bear import deliver_to_bear, deliver_to_stdout` |
| `delivery/dispatch.py` | `from delivery.slack import deliver_to_slack` | `from cyberbriefing.delivery.slack import deliver_to_slack` |
| `delivery/slack.py` | `from delivery.slack_format import markdown_to_block_groups` | `from cyberbriefing.delivery.slack_format import markdown_to_block_groups` |
| `prioritiser/deduplicator.py` | `from prioritiser.claude_response import …` | `from cyberbriefing.prioritiser.claude_response import …` |
| `prioritiser/scorer.py` | `from prioritiser.claude_response import extract_json_text` | `from cyberbriefing.prioritiser.claude_response import extract_json_text` |
| `prioritiser/scorer.py` | `from prioritiser.deduplicator import reconcile_cluster_ids` | `from cyberbriefing.prioritiser.deduplicator import reconcile_cluster_ids` |

(Intra-package imports could be written relative, e.g. `from .backup import …`
inside `delivery/`. We use absolute `cyberbriefing.*` uniformly for consistency.)

### Tests (import lines only)

All 21 test import lines that reference a moved name get the `cyberbriefing.`
prefix, e.g.:

- `from db.state import …` → `from cyberbriefing.db.state import …`
- `from briefing import …` → `from cyberbriefing.briefing import …`
- `import config_loader` → `from cyberbriefing import config_loader`
- `import delivery.dispatch as dispatch_mod` → `import cyberbriefing.delivery.dispatch as dispatch_mod`
- `import prioritiser.scorer as scorer_mod` → `import cyberbriefing.prioritiser.scorer as scorer_mod`
- `import delivery.slack as slack_mod` → `import cyberbriefing.delivery.slack as slack_mod`
- `import weekly_run as weekly_mod` → `import cyberbriefing.weekly_run as weekly_mod`
- …and the remaining `from {collectors,delivery,prioritiser,weekly,db} import …` lines.

**No other test edits needed.** Every monkeypatch targets an *imported module
object* (`monkeypatch.setattr(slack_mod, …)`, `setattr(config_loader, …)`,
`setattr(scorer_mod.anthropic, …)`, `setattr(dispatch_mod, …)`), not a
`patch("dotted.string")` path — verified there are zero string-based patch
targets referencing moved modules. Updating the import alias is sufficient.

## Path-anchor changes

Exactly three anchors change (all currently `Path(__file__).parent`, all needing
the repo root, which is `.parents[2]` from a module directly inside
`src/cyberbriefing/`):

| File:line | Before | After |
|---|---|---|
| `config_loader.py:23` | `_DIR = Path(__file__).parent` | `_DIR = Path(__file__).parents[2]` |
| `briefing.py:555` | `Path(__file__).parent / ".env"` | `Path(__file__).parents[2] / ".env"` |
| `weekly_run.py:112` | `Path(__file__).parent / ".env"` | `Path(__file__).parents[2] / ".env"` |

**Unchanged anchors** (module-relative, travel with the module):

- `prioritiser/scorer.py:20` — `Path(__file__).parent / "prompt.txt"`
- `prioritiser/deduplicator.py:22` — `Path(__file__).parent / "dedup_prompt.txt"`
- `weekly/summariser.py:17` — `Path(__file__).parent / "prompt.txt"`

Also unchanged: `~/cyberbriefing-output/` (home-anchored). During implementation,
re-run a `Path(__file__)` sweep to confirm no anchor was missed.

## `pyproject.toml`

Add a build backend (this is what flips uv from a *virtual* project — which never
installs the code and leaves the existing `cyberbriefing` script non-functional —
to an installed editable package) and the package/scripts config:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cyberbriefing"]

[project.scripts]
cyberbriefing = "cyberbriefing.briefing:main"
cyberbriefing-weekly = "cyberbriefing.weekly_run:main"
```

The existing `[project]` (name `cyberbriefing`, deps) and `[dependency-groups]`
are unchanged. No `[tool.pytest.ini_options] pythonpath` is required: `uv run`
syncs the editable package first, so `import cyberbriefing.*` resolves in tests.

## Invocation surface

Move off `python <file>.py` onto the console scripts:

| File | Before | After |
|---|---|---|
| `com.cyberbriefing.daily.plist.example` | `python` + `briefing.py` | `cyberbriefing` |
| `com.cyberbriefing.daily.laptop.plist.example` | `python` + `briefing.py` | `cyberbriefing` |
| `com.cyberbriefing.weekly.plist.example` | `python` + `weekly_run.py` | `cyberbriefing-weekly` |
| `com.cyberbriefing.weekly.laptop.plist.example` | `python` + `weekly_run.py` | `cyberbriefing-weekly` |
| `install_launchd.sh` | `uv run python briefing.py --gather-only` | `uv run cyberbriefing --gather-only` |

Each plist runs `caffeinate -is /opt/homebrew/bin/uv run --directory
__PROJECT_DIR__ <script>`; the two `<string>python</string>` +
`<string>briefing.py</string>` (or `weekly_run.py`) array elements collapse to a
single `<string>cyberbriefing</string>` (or `cyberbriefing-weekly`). Because
`--directory __PROJECT_DIR__` keeps cwd at the repo root, the `.parents[2]`
anchors resolve to the real `config.yaml` / `.env`. Prose comments in the plists
that mention `briefing.py`/`weekly_run.py` are updated for accuracy.

`healthcheck.sh` — the network probe imports only stdlib (`socket`, `sys`), so it
needs no change; still grep the whole file for stray `briefing.py`/`weekly_run.py`
references and update any found.

## Docs

Update path and command references in:

- **`CLAUDE.md`** — the Architecture tree (`prioritiser/scorer.py` →
  `src/cyberbriefing/prioritiser/scorer.py`, etc.), "Running it" commands
  (`uv run python briefing.py --dry-run` → `uv run cyberbriefing --dry-run`), the
  weekly commands (`uv run python weekly_run.py` → `uv run cyberbriefing-weekly`),
  and the Scheduling section's `python briefing.py` references.
- **`README.md`** — any run commands / structure references.
- **`HANDOFF.md`** — any file-path or command references.

## What explicitly does NOT change

- Runtime behaviour of the pipelines (pure refactor).
- Location of `config.yaml`, `config.local.yaml`, `.env` (repo root).
- Location of `tests/` (repo root).
- `.txt` prompt-file resolution (module-relative).
- `~/cyberbriefing-output/` backup location.
- `.gitignore` (its globals — `__pycache__/`, `*.pyc`, `.env`,
  `config.local.yaml`, `*.plist`/`!*.plist.example` — still apply under `src/`).
- `uv.lock` dependency set (no new *runtime* deps; `hatchling` is a build
  requirement pulled at build time, not a project dependency).

## Verification / definition of done

1. `uv sync` succeeds (package builds and installs editable).
2. `uv run pytest` — all tests green.
3. `uv run cyberbriefing --dry-run` produces a briefing to stdout.
4. `uv run cyberbriefing-weekly --dry-run` produces a weekly summary to stdout.
5. `git grep -nE "^(from|import) (config_loader|collectors|db|delivery|prioritiser|weekly|briefing|weekly_run)\b" -- '*.py'`
   returns nothing (no stale flat imports remain).
6. `git grep -nE "python (briefing|weekly_run)\.py"` returns nothing outside
   historical spec/plan docs (no stale invocations in plists/scripts/live docs).

## Risks and mitigations

- **Missed import or anchor** → the verification greps (5) and the `Path(__file__)`
  sweep catch these; `uv run pytest` exercises every module's import.
- **uv doesn't install the package** (missing/incorrect build-system) → caught by
  step 1/2 failing to import `cyberbriefing`; fix the `[build-system]` /
  `[tool.hatch.build.targets.wheel]` config.
- **Plist regression on the real host** → the `.example` templates are updated
  here; the operator re-installs from the template (the real `.plist` is
  gitignored and re-created per the existing install flow). Call this out so the
  host's launchd agents get re-bootstrapped after the change.

## Out of scope

- Renaming modules or reorganising within the package.
- Any change to scoring, collectors, delivery, or scheduling logic.
- Adding `requirements.txt` or a pip path (explicitly forbidden by CLAUDE.md).
