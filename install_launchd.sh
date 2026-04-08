#!/bin/bash
# Installs com.cyberbriefing.daily as a launchd agent and runs a dry-run test.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_SRC="$SCRIPT_DIR/com.cyberbriefing.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.cyberbriefing.daily.plist"
UV="/opt/homebrew/bin/uv"

echo "=== Step 1: Installing plist to LaunchAgents ==="
sed "s|__PROJECT_DIR__|$SCRIPT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
echo "Installed to $PLIST_DST (paths set to $SCRIPT_DIR)"

echo ""
echo "=== Step 2: Loading with launchctl ==="
launchctl load "$PLIST_DST"
echo "Loaded."

echo ""
echo "=== Step 3: Verifying it loaded ==="
launchctl list | grep cyberbriefing && echo "✓ Agent is registered." || echo "✗ Not found in launchctl list — check for errors above."

echo ""
echo "=== Step 4: Full run (creates Bear note) ==="
cd "$SCRIPT_DIR" && "$UV" run python briefing.py

echo ""
echo "=== Step 5: Checking launchctl status and recent logs ==="
echo "--- launchctl list entry ---"
launchctl list com.cyberbriefing.daily 2>/dev/null || echo "(not currently listed)"
echo ""
echo "--- Last 50 lines of /tmp/cyberbriefing.log ---"
tail -50 /tmp/cyberbriefing.log 2>/dev/null || echo "(no log file yet)"
echo ""
echo "--- Last 50 lines of /tmp/cyberbriefing.err ---"
tail -50 /tmp/cyberbriefing.err 2>/dev/null || echo "(no error file yet)"
echo ""
echo "--- Recent system log entries for cyberbriefing ---"
log show --last 1h --predicate 'eventMessage CONTAINS "cyberbriefing" OR processImagePath CONTAINS "cyberbriefing"' 2>/dev/null | tail -30 || echo "(no log entries found)"
