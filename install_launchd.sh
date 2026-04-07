#!/bin/bash
# Installs com.cyberbriefing.daily as a launchd agent and runs a dry-run test.

set -e

PLIST_SRC="$HOME/Documents/Program/scripts/cyberbriefing/com.cyberbriefing.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.cyberbriefing.daily.plist"
PYTHON="$HOME/Documents/Program/scripts/cyberbriefing/.venv/bin/python"
SCRIPT="$HOME/Documents/Program/scripts/cyberbriefing/briefing.py"

echo "=== Step 1: Copying plist to LaunchAgents ==="
cp "$PLIST_SRC" "$PLIST_DST"
echo "Copied to $PLIST_DST"

echo ""
echo "=== Step 2: Loading with launchctl ==="
launchctl load "$PLIST_DST"
echo "Loaded."

echo ""
echo "=== Step 3: Verifying it loaded ==="
launchctl list | grep cyberbriefing && echo "✓ Agent is registered." || echo "✗ Not found in launchctl list — check for errors above."

echo ""
echo "=== Step 4: Full run (creates Bear note) ==="
"$PYTHON" "$SCRIPT"

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
