#!/usr/bin/env bash
# Cyber Briefing pre-flight health check.
# Verifies every condition we know has broken a morning fire in the past.
# Run any time: ./healthcheck.sh

set -u

REPO="/Users/duncan/Developer/scripts/cyberbriefing"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/com.cyberbriefing.daily.plist"
PLIST_REPO="$REPO/com.cyberbriefing.daily.plist"
LABEL="com.cyberbriefing.daily"
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

hdr "4. launchd agent"
if [[ -f "$PLIST_INSTALLED" ]]; then
    ok "plist installed at $PLIST_INSTALLED"
    if cmp -s "$PLIST_INSTALLED" "$PLIST_REPO"; then
        ok "installed plist matches repo copy"
    else
        warn "installed plist DIFFERS from repo copy — re-install if you've edited it"
    fi
else
    bad "plist not installed at $PLIST_INSTALLED"
fi

PRINT=$(launchctl print "gui/$(id -u)/$LABEL" 2>&1)
if [[ "$PRINT" == *"Could not find service"* ]]; then
    bad "agent not loaded — run install_launchd.sh"
else
    ok "agent loaded in gui/$(id -u)"

    SPAWN=$(printf '%s\n' "$PRINT" | awk -F'= ' '/spawn type/ {print $2; exit}')
    case "$SPAWN" in
        "interactive (4)") ok "spawn type = interactive (4) — correct Aqua context" ;;
        *) bad "spawn type = ${SPAWN:-unknown} — needs interactive (4); was the 4 May regression" ;;
    esac

    LAST_EXIT=$(printf '%s\n' "$PRINT" | awk -F'= ' '/last exit code/ {print $2; exit}')
    if [[ -n "$LAST_EXIT" && "$LAST_EXIT" != "0" ]]; then
        warn "last exit code = $LAST_EXIT — check /tmp/cyberbriefing.err"
    else
        ok "last exit code = ${LAST_EXIT:-n/a}"
    fi
fi

hdr "5. Files & secrets"
[[ -f "$REPO/.env" ]] && ok ".env present" || bad ".env MISSING in $REPO"
if [[ -f "$REPO/.env" ]] && grep -q '^ANTHROPIC_API_KEY=' "$REPO/.env"; then
    ok "ANTHROPIC_API_KEY set in .env"
else
    bad "ANTHROPIC_API_KEY missing from .env"
fi
[[ -f "$STATE_DB" ]] && ok "state.db present" || bad "state.db missing at $STATE_DB"
[[ -d "$OUTPUT_DIR" ]] && ok "output dir present" || warn "output dir missing — will be created on first run"

hdr "6. Network resolution via uv python (the real TripMode test)"
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

hdr "7. Today's run status"
TODAY_NOTE="$OUTPUT_DIR/Cyber Briefing _ $TODAY.md"
TODAY_FAIL="$OUTPUT_DIR/FAILURE-$TODAY.md"
[[ -f "$TODAY_NOTE" ]] && ok "today's briefing markdown exists: $(basename "$TODAY_NOTE")"
if [[ -f "$TODAY_FAIL" ]]; then
    warn "FAILURE marker present for today: $(basename "$TODAY_FAIL")"
fi
if [[ ! -f "$TODAY_NOTE" && ! -f "$TODAY_FAIL" ]]; then
    echo "    No run yet today (expected if before 06:15 or this is a fresh day)."
fi

RECENT_FAILS=$(find "$OUTPUT_DIR" -maxdepth 1 -name 'FAILURE-*.md' -mtime -7 2>/dev/null | wc -l | tr -d ' ')
if [[ "$RECENT_FAILS" -gt 0 ]]; then
    warn "$RECENT_FAILS FAILURE-*.md file(s) in the last 7 days"
fi

hdr "Summary"
printf "  %d passed, %d warning(s), %d failure(s)\n" "$PASS" "$WARN" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
elif [[ "$WARN" -gt 0 ]]; then
    exit 2
fi
exit 0
