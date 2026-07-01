# Slack delivery — design

**Date:** 2026-07-01
**Branch:** `slack-delivery`
**Status:** design, awaiting review

## Goal

Add Slack as a **selectable delivery target** for the cyber briefing, alongside
the existing Bear Notes delivery. Both remain fully working; a user chooses
between them with a single `delivery.method` value in `config.yaml`. The choice
applies to **both** pipelines — the daily briefing (`briefing.py`) and the
Sunday weekly summary (`weekly_run.py`).

Delivery lands in a channel as a **native Slack message** (converted `mrkdwn`),
with any content that exceeds Slack's per-message limits pushed into **threaded
replies** under the parent message.

## Non-goals

- Not removing or changing Bear delivery behaviour (beyond moving the markdown
  backup write out of it — see §4).
- Not replacing `stdout` / `markdown_file` methods.
- Not adding file/snippet uploads to Slack (native messages only → the only
  scope required is `chat:write`).
- Not solving the 1Password unattended-unlock caveat (see §9); only recording it.

## 1. Config & secrets

`delivery.method` gains a `slack` value:

```yaml
delivery:
  method: "bear"          # bear | slack | stdout | markdown_file
  bear_tag: "security/briefing/daily"
  markdown_output_dir: "~/cyberbriefing-output"
  slack:
    channel: "C0BE6PB6S75"  # channel ID (stable across renames); the bot must be a member
```

- **Token:** `SLACK_BOT_TOKEN`, read from the environment after `load_dotenv()`.
  Sourced via the **1Password local env file** feature (beta): the `.env` is a
  mounted UNIX FIFO, 1Password streams the value on read, and standard
  `python-dotenv` loads it with no code changes and no `op run` wrapper. The
  channel is not a secret and lives in `config.yaml`.
- **HTTP:** use `requests` (already a project dependency). No new packages, no
  `slack_sdk`.
- **`.env.example`:** replace the stale `op run …` comment with a note pointing
  at the 1Password local env file, and add a `SLACK_BOT_TOKEN=` line.

## 2. Two decisions that shape the design

### (a) The markdown backup must be written in Slack mode too

`weekly/reader.py` builds the Sunday summary by reading the **daily markdown
backups** in `~/cyberbriefing-output/`. That is a hard dependency. Today the
backup is written as a side effect of `deliver_to_bear()`. If Slack mode simply
skipped Bear, the backup would disappear and the weekly pipeline would silently
break.

**Resolution:** centralise backup-writing into a dispatcher that always writes
it regardless of method (except `stdout`, which is ephemeral). `deliver_to_bear()`
is slimmed to "get it into Bear only." Net effect: the durable local `.md` is
always written for `bear` / `slack` / `markdown_file`, matching the tool's
existing "preserved somewhere" philosophy — nothing is ever lost.

### (b) Convert the markdown string — do not build a second structured renderer

Daily and weekly have different internal structures but both emit the same
`(title, body, tags)` markdown. A single **markdown → Slack blocks converter**
therefore serves both pipelines and keeps the dispatcher interface uniform.

**Correctness trap:** our formatters use `*…*` for *italic* (Score lines, source
captions), but in Slack `*…*` means **bold** and italic is `_…_`. A naive
pass-through would render every italic as bold. The converter maps this
explicitly: handle `**b**` / `__b__` → `*b*` first, then `*i*` / `_i_` → `_i_`.
This is the main reason to convert deliberately rather than forward the string.

## 3. Components

| Module | Responsibility |
|--------|----------------|
| `delivery/dispatch.py` (new) | `deliver(delivery_cfg, title, body, tags) -> bool`. Route on `method`, then always write the markdown backup (except `stdout`). Return value = "briefing preserved" (backup written), so a flaky Slack post never reports total failure. |
| `delivery/backup.py` (new) | `write_markdown_backup(...)` + `_prune_old_markdown_files(...)`, moved verbatim out of `bear.py` so both paths share one implementation. |
| `delivery/slack.py` (new) | `deliver_to_slack(title, body, tags, slack_cfg) -> bool`. Build block groups, post the parent via `chat.postMessage`, post overflow as threaded replies. Handle `ok:false`, HTTP errors, 429 `Retry-After`, and missing token. |
| `delivery/slack_format.py` (new) | The converter: markdown body → ordered list of block groups (first = parent message, rest = thread replies). |
| `delivery/bear.py` (edit) | Drop the backup call; `deliver_to_bear()` returns Bear success only. |
| `briefing.py` (edit) | Replace the inline `if method ==` block with `deliver(...)`. Route the scoring-failure escalation through `deliver(...)` too, so it honours the configured channel instead of hardcoding Bear. `--dry-run` still calls `deliver_to_stdout` directly (unchanged). |
| `weekly_run.py` (edit) | Load the `delivery` config block (it currently loads only `scoring`) and call `deliver(...)` instead of the hardcoded `deliver_to_bear`. |

## 4. Data flow

The `(title, body, tags)` contract is unchanged everywhere:

```
title, body, tags = format_briefing(...)        # or format_weekly(...)
success = deliver(config["delivery"], title, body, tags)
```

Dispatcher logic:

```
def deliver(delivery_cfg, title, body, tags) -> bool:
    method = delivery_cfg.get("method", "bear")
    if method == "stdout":
        return deliver_to_stdout(title, body, tags)   # ephemeral, no backup
    if method == "slack":
        deliver_to_slack(title, body, tags, delivery_cfg.get("slack", {}))
    elif method == "bear":
        deliver_to_bear(title, body, tags)             # Bear only now
    elif method == "markdown_file":
        pass                                           # backup below IS the delivery
    else:
        log.error("unknown delivery method: %s", method)
    return write_markdown_backup(title, body, tags)    # always; weekly reader depends on it
```

Channel delivery is best-effort and logged; the backup write is the success
criterion. This preserves the current behaviour where "today's briefing is
preserved somewhere" counts as success.

## 5. Markdown → Slack conversion rules

Inline conversion (`_md_inline_to_mrkdwn`):

- `[text](url)` → `<url|text>`
- `**b**` / `__b__` → `*b*`  (bold; do this before single-delimiter italic)
- `*i*` / `_i_` → `_i_`      (italic)

Block-level handling:

- `# / ## / ###` headings → a `header` block (top title) or a bold section line
  (tier headers), with any leading emoji preserved.
- `- ` list items (Britain section bullets) → mrkdwn bullet lines in a section
  block.
- `---` dividers → a `divider` block (or a section boundary).
- Bear `#security/briefing/...` tags are delivery metadata for Bear and are
  **omitted** from Slack output.

## 6. Block grouping & thread overflow

Slack limits to respect:

- ≤ 50 blocks per message (use a conservative budget, ~45).
- section block `text` ≤ 3000 chars (split long chunks).
- `header` block text ≤ 150 chars, plain-text.

Strategy (deterministic, in briefing order):

1. Split the body at `---` / `##` boundaries into ordered chunks.
2. Convert each chunk to section block(s), splitting any chunk whose text
   exceeds 3000 chars.
3. Pack blocks into groups of ≤ ~45. The **first group** is the parent message
   (title header + summary + the first tiers that fit — Critical/Notable);
   remaining groups are posted as **threaded replies** in order.

## 7. Slack client behaviour (`delivery/slack.py`)

- Endpoint: `POST https://slack.com/api/chat.postMessage`
- Headers: `Authorization: Bearer <SLACK_BOT_TOKEN>`,
  `Content-Type: application/json; charset=utf-8`
- Parent payload: `{channel, text: <title fallback>, blocks: <group[0]>}`.
  Always send `text` for notifications/accessibility.
- Capture `ts` from the parent response; post each remaining group with
  `thread_ts=<ts>`.
- Error handling:
  - Missing `SLACK_BOT_TOKEN` → log error, return `False` (dispatcher still
    writes the backup).
  - HTTP error / timeout → log, return `False`.
  - JSON `{"ok": false, "error": ...}` → log the Slack error, return `False`.
  - HTTP 429 → honour `Retry-After` (bounded retries) then continue.
- Return `True` iff the **parent** message posted; thread replies are
  best-effort and their failures are logged, not fatal.

## 8. Testing (TDD)

New pytest modules (under existing `tests/` setup):

- **Converter** (`slack_format`): link conversion; the italic/bold trap
  (`*x*`→`_x_`, `**x**`→`*x*`); headings; bullets; dividers; 3000-char split;
  thread grouping when blocks exceed the parent budget; title header ≤ 150 chars.
  Test against **both** a realistic daily body (`format_briefing`) and a weekly
  body (`format_weekly`) — the converter must handle whatever markdown constructs
  `weekly/formatter.py` actually emits, not only the daily formatter's. Confirm
  that set during implementation before finalising the converter's grammar.
- **Slack client** (`slack`): mocked `requests.post` — parent payload shape;
  `thread_ts` used on replies; `ok:false` → `False` + logged; 429 retry then
  success; missing token → `False`.
- **Dispatcher** (`dispatch`): method routing (bear/slack/stdout/markdown_file/
  unknown); backup-always-written invariant for bear/slack/markdown_file;
  stdout skips backup; return semantics.

## 9. One-time setup (user)

1. Create a Slack app → add the `chat:write` bot scope → install to the
   workspace.
2. `/invite` the bot into the target channel.
3. Put the channel ID in `config.yaml` (`delivery.slack.channel`) and set
   `delivery.method: slack`.
4. Provide `SLACK_BOT_TOKEN` via the 1Password local env file mounted at `.env`.

**Operational caveat (recorded, not solved):** the 1Password local env file
prompts for authorization on first read after 1Password *locks*, and the FIFO
does not support concurrent readers. For the unattended launchd fires
(06:15 / 07:30 daily, 12:00 / 13:30 Sunday) to obtain the token, 1Password must
stay unlocked. The daily and weekly fire windows do not overlap, so the
single-reader limit is not a concern.

## 10. Resolved decisions

- **Channel:** `C0BE6PB6S75`, stored in `config.yaml` under
  `delivery.slack.channel` — in config, never in code. (Confirmed 2026-07-01.)
- **`markdown_file` method:** kept as a first-class method (it becomes "backup
  only, no channel" now that the dispatcher writes the backup for every method).
- **Backup location:** unchanged — `~/cyberbriefing-output/`, filename = the
  sanitised note title (e.g. `Cyber Briefing _ 2026-07-01.md`), pruned after 10
  days. This is the directory `weekly/reader.py` reads.
- **`markdown_output_dir` config key:** stays **inert** (as today — it is read
  nowhere; the path is hardcoded in `bear.py`, `briefing.py`, `weekly_run.py`,
  and `weekly/reader.py`). Wiring it up is out of scope for this change.
