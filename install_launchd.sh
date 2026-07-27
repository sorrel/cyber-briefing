#!/bin/bash
# Installs the cyberbriefing launchd agents (daily + weekly) for this machine.
#
# Generates the real, gitignored plists from the committed *.plist.example
# templates by filling in the __PROJECT_DIR__ / __USER__ placeholders, then
# bootstraps them into the Aqua GUI session.
#
#   ./install_launchd.sh              # always-on desktop archetype
#   ./install_launchd.sh --laptop     # sleeping-laptop archetype
#   ./install_launchd.sh --daily      # only the daily agent
#   ./install_launchd.sh --weekly     # only the weekly agent
#
# See CLAUDE.md ("Deployment environment" / "Scheduling") for the difference
# between the two archetypes.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
UV="${UV:-/opt/homebrew/bin/uv}"
GUI="gui/$(id -u)"

ARCHETYPE=""        # "" = always-on desktop, ".laptop" = sleeping laptop
AGENTS=()

usage() {
    cat <<'USAGE'
Installs the cyberbriefing launchd agents from the committed *.plist.example
templates, filling in the __PROJECT_DIR__ / __USER__ placeholders.

  ./install_launchd.sh              both agents, always-on desktop archetype
  ./install_launchd.sh --laptop     sleeping-laptop archetype (later fires, no pmset)
  ./install_launchd.sh --desktop    always-on desktop archetype (the default)
  ./install_launchd.sh --daily      only the daily agent
  ./install_launchd.sh --weekly     only the weekly agent

Flags combine, e.g. --laptop --weekly. Any existing real plist is backed up
first. See CLAUDE.md ("Deployment environment" / "Scheduling") for the
difference between the archetypes.
USAGE
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --laptop)  ARCHETYPE=".laptop" ;;
        --desktop) ARCHETYPE="" ;;
        --daily)   AGENTS+=("daily") ;;
        --weekly)  AGENTS+=("weekly") ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1" >&2; usage 1 ;;
    esac
    shift
done
[[ ${#AGENTS[@]} -eq 0 ]] && AGENTS=("daily" "weekly")

if [[ -n "$ARCHETYPE" ]]; then
    echo "Archetype: sleeping laptop (no pmset wake; later fire times)"
else
    echo "Archetype: always-on desktop (needs the pmset wake — see step 4)"
fi
echo "Project dir: $SCRIPT_DIR"

echo ""
echo "=== Step 0: Ensuring scripts are executable ==="
chmod +x "$SCRIPT_DIR/install_launchd.sh"
chmod +x "$SCRIPT_DIR/healthcheck.sh"
echo "Done."

echo ""
echo "=== Step 1: Generating plists from the committed templates ==="
for agent in "${AGENTS[@]}"; do
    label="com.cyberbriefing.$agent"
    template="$SCRIPT_DIR/$label$ARCHETYPE.plist.example"
    repo_plist="$SCRIPT_DIR/$label.plist"

    if [[ ! -f "$template" ]]; then
        echo "✗ Template missing: $template" >&2
        exit 1
    fi

    # Never silently discard a hand-edited local plist (schedules often get
    # tweaked per machine) — keep a timestamped copy alongside it.
    if [[ -f "$repo_plist" ]]; then
        backup="$repo_plist.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$repo_plist" "$backup"
        echo "  ! $label.plist already existed — backed up to $(basename "$backup")"
    fi

    sed -e "s|__PROJECT_DIR__|$SCRIPT_DIR|g" \
        -e "s|__USER__|$(id -un)|g" \
        "$template" > "$repo_plist"

    if grep -q '__PROJECT_DIR__\|__USER__' "$repo_plist"; then
        echo "✗ Unsubstituted placeholder left in $repo_plist" >&2
        exit 1
    fi
    plutil -lint "$repo_plist" > /dev/null
    echo "  ✓ $(basename "$repo_plist") generated from $(basename "$template")"
done

echo ""
echo "=== Step 2: Installing and bootstrapping into the Aqua GUI session ==="
# bootout/bootstrap rather than the deprecated `launchctl load`: only this pair
# reliably yields the interactive (4) spawn type that the DNS/mach-port context
# depends on (see the EBADF sections in CLAUDE.md).
mkdir -p "$LAUNCH_AGENTS"
for agent in "${AGENTS[@]}"; do
    label="com.cyberbriefing.$agent"
    cp "$SCRIPT_DIR/$label.plist" "$LAUNCH_AGENTS/$label.plist"
    launchctl bootout "$GUI/$label" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$LAUNCH_AGENTS/$label.plist"
    echo "  ✓ $label installed and bootstrapped"
done

echo ""
echo "=== Step 3: Verifying spawn context ==="
for agent in "${AGENTS[@]}"; do
    label="com.cyberbriefing.$agent"
    spawn=$(launchctl print "$GUI/$label" 2>/dev/null | awk -F'= ' '/spawn type/ {print $2; exit}')
    case "$spawn" in
        "interactive (4)") echo "  ✓ $label — spawn type = interactive (4)" ;;
        "") echo "  ✗ $label — not loaded; check for errors above" ;;
        *) echo "  ✗ $label — spawn type = $spawn (needs interactive (4) for a working DNS context)" ;;
    esac
done

echo ""
echo "=== Step 4: Wake schedule ==="
if [[ -n "$ARCHETYPE" ]]; then
    echo "  Laptop archetype — no pmset wake (a closed lid can't be woken)."
    echo "  launchd runs the missed calendar job on the next wake."
else
    if pmset -g sched 2>/dev/null | grep -q "wakepoweron at 6:10AM"; then
        echo "  ✓ 06:10 wakepoweron already scheduled"
    else
        echo "  ! No 06:10 wake scheduled. An always-on Mac sits in dark wake"
        echo "    overnight, where getaddrinfo fails with EBADF. Run:"
        echo "      sudo pmset repeat wakeorpoweron MTWRF 06:10:00"
    fi
fi

echo ""
echo "=== Step 5: Gather-only test (no scoring, no API calls, no delivery) ==="
cd "$SCRIPT_DIR" && "$UV" run cyberbriefing --gather-only

echo ""
echo "=== Step 6: Recent logs ==="
for pair in "daily:/tmp/cyberbriefing" "weekly:/tmp/cyberbriefing-weekly"; do
    agent="${pair%%:*}"; prefix="${pair#*:}"
    [[ " ${AGENTS[*]} " == *" $agent "* ]] || continue
    for stream in log err; do
        echo "--- last 20 lines of $prefix.$stream ---"
        tail -20 "$prefix.$stream" 2>/dev/null || echo "(no file yet)"
    done
done

echo ""
echo "Done. Run ./healthcheck.sh any time for a full pre-flight check."
