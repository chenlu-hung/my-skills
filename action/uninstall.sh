#!/usr/bin/env bash
# Reverse of install.sh. Keeps the queue + logs under ~/.claude/action.
set -euo pipefail

BASE="$HOME/.claude/action"
LABEL=com.luhung.action
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "launchd agent removed: $LABEL"

[ -f "$HOME/.local/bin/action" ] && rm -f "$HOME/.local/bin/action" && echo "wrapper removed"

echo "kept queue + logs under $BASE (delete manually if you want them gone)"
