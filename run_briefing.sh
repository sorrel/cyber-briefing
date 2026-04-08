#!/bin/bash
# Wrapper for launchd: wait for network and retry the briefing if needed.
# [Errno 9] Bad file descriptor occurs when launchd fires before the
# network stack is ready (e.g. wake from sleep at 06:00).

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_PREFIX="[cyberbriefing-wrapper]"
MAX_RETRIES=3
RETRY_DELAY=30

echo "$LOG_PREFIX Checking network availability..."

# Wait up to 90 seconds for network.
# Use curl to test a real HTTPS connection — DNS alone can succeed
# before TCP sockets are ready, causing [Errno 9] EBADF in Python.
for i in $(seq 1 18); do
    if /usr/bin/curl -sf --connect-timeout 5 --max-time 8 https://www.google.com > /dev/null 2>&1; then
        echo "$LOG_PREFIX Network is up (attempt $i). Starting briefing."
        break
    fi
    if [ "$i" -eq 18 ]; then
        echo "$LOG_PREFIX Network not available after 90 seconds — aborting."
        exit 1
    fi
    echo "$LOG_PREFIX Network not ready (attempt $i/18), waiting 5 seconds..."
    sleep 5
done

cd "$SCRIPT_DIR"

# Retry the briefing if it gathers zero items (network may pass curl
# check but still be flaky for Python's connection pool).
for attempt in $(seq 1 "$MAX_RETRIES"); do
    echo "$LOG_PREFIX Briefing attempt $attempt/$MAX_RETRIES"
    output=$(/opt/homebrew/bin/uv run python briefing.py 2>&1)
    exit_code=$?
    echo "$output" >&2

    # Success: either items were gathered or the script exited cleanly
    # without EBADF errors.
    if [ $exit_code -eq 0 ] && ! echo "$output" | grep -q "Bad file descriptor"; then
        echo "$LOG_PREFIX Briefing completed successfully on attempt $attempt."
        exit 0
    fi

    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
        echo "$LOG_PREFIX Attempt $attempt failed (EBADF or error). Retrying in ${RETRY_DELAY}s..."
        sleep "$RETRY_DELAY"
    fi
done

echo "$LOG_PREFIX All $MAX_RETRIES attempts failed."
exit 1
