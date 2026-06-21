#!/usr/bin/env bash
# Cyber Briefing pre-flight health check.
# Verifies every condition we know has broken a morning fire in the past.
# Run any time: ./healthcheck.sh

set -u

REPO="/Users/duncan/Developer/scripts/cyberbriefing"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/com.cyberbriefing.daily.plist"
PLIST_REPO="$REPO/com.cyberbriefing.daily.plist"
LABEL="com.cyberbriefing.daily"
WEEKLY_PLIST_INSTALLED="$HOME/Library/LaunchAgents/com.cyberbriefing.weekly.plist"
WEEKLY_PLIST_REPO="$REPO/com.cyberbriefing.weekly.plist"
WEEKLY_LABEL="com.cyberbriefing.weekly"
OUTPUT_DIR="$HOME/cyberbriefing-output"
STATE_DB="$HOME/.cyberbriefing/state.db"
TODAY="$(date +%Y-%m-%d)"

PASS=0
WARN=0
FAIL=0

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; WARN=$((WARN+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

# Verify a plist file is installed and matches the repo copy.
# Args: <installed-path> <repo-path>
check_plist_file() {
    local installed="$1" repo="$2"
    if [[ -f "$installed" ]]; then
        ok "plist installed at $installed"
        if cmp -s "$installed" "$repo"; then
            ok "installed plist matches repo copy"
        else
            warn "installed plist DIFFERS from repo copy — re-install if you've edited it"
        fi
    else
        bad "plist not installed at $installed"
    fi
}

# Verify a launchd agent is loaded in the Aqua GUI context with a clean exit.
# Args: <label> <error-log-path>
check_agent() {
    local label="$1" errlog="$2" print spawn last_exit
    print=$(launchctl print "gui/$(id -u)/$label" 2>&1)
    if [[ "$print" == *"Could not find service"* ]]; then
        bad "agent $label not loaded — install it (see CLAUDE.md / install_launchd.sh)"
        return
    fi
    ok "agent $label loaded in gui/$(id -u)"

    spawn=$(printf '%s\n' "$print" | awk -F'= ' '/spawn type/ {print $2; exit}')
    case "$spawn" in
        "interactive (4)") ok "spawn type = interactive (4) — correct Aqua context" ;;
        *) bad "spawn type = ${spawn:-unknown} — needs interactive (4); was the 4 May regression" ;;
    esac

    last_exit=$(printf '%s\n' "$print" | awk -F'= ' '/last exit code/ {print $2; exit}')
    if [[ -n "$last_exit" && "$last_exit" != "0" ]]; then
        warn "last exit code = $last_exit — check $errlog"
    else
        ok "last exit code = ${last_exit:-n/a}"
    fi
}

hdr "1. TripMode (known cause of EBADF on 2026-05-15)"
if pgrep -xq TripMode; then
    warn "TripMode is running — verify uv/python are allowed on this network"
    echo "    The real test is the uv DNS probe below; if that passes, you're fine."
else
    ok "TripMode not running"
fi

hdr "2. Bear (delivery target)"
if pgrep -xq Bear; then
    ok "Bear is running"
else
    warn "Bear not running — cold-launch path will run; markdown backup is always written"
fi

hdr "3. pmset wake schedule (06:10 user-session wake — fixes dark-wake EBADF)"
if pmset -g sched 2>/dev/null | grep -q "wakepoweron at 6:10AM every day"; then
    ok "Daily 06:10 wakepoweron is set"
else
    bad "06:10 daily wake is NOT scheduled — run: sudo pmset repeat wakeorpoweron MTWRFSU 06:10:00"
fi

hdr "4. Daily launchd agent (06:15 / 07:30)"
check_plist_file "$PLIST_INSTALLED" "$PLIST_REPO"
check_agent "$LABEL" "/tmp/cyberbriefing.err"

hdr "5. Weekly launchd agent (Sunday 12:00 / 13:30)"
check_plist_file "$WEEKLY_PLIST_INSTALLED" "$WEEKLY_PLIST_REPO"
check_agent "$WEEKLY_LABEL" "/tmp/cyberbriefing-weekly.err"
echo "    (No pmset wake needed — the weekly fires at midday when the Mac is awake.)"

hdr "6. Files & secrets"
[[ -f "$REPO/.env" ]] && ok ".env present" || bad ".env MISSING in $REPO"
if [[ -f "$REPO/.env" ]] && grep -q '^ANTHROPIC_API_KEY=' "$REPO/.env"; then
    ok "ANTHROPIC_API_KEY set in .env"
else
    bad "ANTHROPIC_API_KEY missing from .env"
fi
[[ -f "$STATE_DB" ]] && ok "state.db present" || bad "state.db missing at $STATE_DB"
[[ -d "$OUTPUT_DIR" ]] && ok "output dir present" || warn "output dir missing — will be created on first run"

hdr "7. Network resolution via uv python (the real TripMode test)"
PROBE=$(cd "$REPO" && /opt/homebrew/bin/uv run --quiet python -c "
import socket, sys
try:
    socket.getaddrinfo('api.anthropic.com', 443)
    print('ok')
except OSError as e:
    print(f'fail: {e}')
    sys.exit(1)
" 2>&1)
if [[ "$PROBE" == "ok" ]]; then
    ok "uv python can resolve api.anthropic.com — DNS path is healthy"
else
    bad "uv python getaddrinfo FAILED: $PROBE"
    echo "    This is the exact failure mode TripMode causes. Allow uv/python in TripMode."
fi

hdr "8. Run status"
TODAY_NOTE="$OUTPUT_DIR/Cyber Briefing _ $TODAY.md"
TODAY_FAIL="$OUTPUT_DIR/FAILURE-$TODAY.md"
[[ -f "$TODAY_NOTE" ]] && ok "today's briefing markdown exists: $(basename "$TODAY_NOTE")"
if [[ -f "$TODAY_FAIL" ]]; then
    warn "FAILURE marker present for today: $(basename "$TODAY_FAIL")"
fi
if [[ ! -f "$TODAY_NOTE" && ! -f "$TODAY_FAIL" ]]; then
    echo "    No daily run yet today (expected if before 06:15 or this is a fresh day)."
fi

WEEKLY_NOTE=$(find "$OUTPUT_DIR" -maxdepth 1 -name 'Weekly Cyber Summary*.md' -mtime -7 2>/dev/null | head -1)
if [[ -n "$WEEKLY_NOTE" ]]; then
    ok "recent weekly summary markdown exists: $(basename "$WEEKLY_NOTE")"
else
    echo "    No weekly summary in the last 7 days (expected before the first Sunday run)."
fi

RECENT_FAILS=$(find "$OUTPUT_DIR" -maxdepth 1 -name 'FAILURE-*.md' -mtime -7 2>/dev/null | wc -l | tr -d ' ')
if [[ "$RECENT_FAILS" -gt 0 ]]; then
    warn "$RECENT_FAILS FAILURE-*.md file(s) in the last 7 days (daily + weekly)"
fi

hdr "Summary"
printf "  %d passed, %d warning(s), %d failure(s)\n" "$PASS" "$WARN" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
elif [[ "$WARN" -gt 0 ]]; then
    exit 2
fi
exit 0
