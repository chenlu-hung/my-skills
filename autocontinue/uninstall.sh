#!/usr/bin/env bash
# Uninstall autocontinue: unload the launchd agent and remove the hook.
# Queue/log data under ~/.claude/autocontinue is kept; delete it manually.
set -euo pipefail

BASE="$HOME/.claude/autocontinue"
PY=/usr/bin/python3
LABEL=com.luhung.autocontinue
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SETTINGS="$HOME/.claude/settings.json"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "launchd agent removed"

if [ -f "$SETTINGS" ]; then
  "$PY" - "$SETTINGS" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
matchers = data.get("hooks", {}).get("StopFailure", [])
matchers[:] = [
    m for m in matchers
    if not any("autocontinue" in h.get("command", "") for h in m.get("hooks", []))
]
if not matchers:
    data.get("hooks", {}).pop("StopFailure", None)
if not data.get("hooks"):
    data.pop("hooks", None)
with open(path, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("hook removed from", path)
PYEOF
fi

echo "data kept at $BASE (remove manually if no longer needed)"
