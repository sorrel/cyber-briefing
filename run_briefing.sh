#!/bin/bash
# Wrapper for launchd: wait for network before running the briefing.
# [Errno 9] Bad file descriptor occurs when launchd fires before the
# network stack is ready (e.g. wake from sleep at 06:00).

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_PREFIX="[cyberbriefing-wrapper]"

echo "$LOG_PREFIX Checking network availability..."

# Wait up to 60 seconds for network.
# Use curl to test a real HTTPS connection — DNS alone can succeed
# before TCP sockets are ready, causing [Errno 9] EBADF in Python.
for i in $(seq 1 12); do
    if /usr/bin/curl -sf --connect-timeout 5 --max-time 8 https://www.google.com > /dev/null 2>&1; then
        echo "$LOG_PREFIX Network is up (attempt $i). Starting briefing."
        break
    fi
    if [ "$i" -eq 12 ]; then
        echo "$LOG_PREFIX Network not available after 60 seconds — aborting."
        exit 1
    fi
    echo "$LOG_PREFIX Network not ready (attempt $i/12), waiting 5 seconds..."
    sleep 5
done

# Use uv run so the venv and deps are always resolved correctly
cd "$SCRIPT_DIR"
exec /opt/homebrew/bin/uv run python briefing.py
