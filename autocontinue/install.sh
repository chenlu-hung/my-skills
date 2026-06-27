#!/usr/bin/env bash
# Install autocontinue: copy scripts, register the StopFailure hook,
# and load the launchd checker agent. Safe to re-run after updates.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/.claude/autocontinue"
PY=/usr/bin/python3
LABEL=com.luhung.autocontinue
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SETTINGS="$HOME/.claude/settings.json"
HOOK_CMD="$PY $BASE/bin/autocontinue_hook.py"

[ -x "$PY" ] || { echo "error: $PY not found (install Xcode Command Line Tools)"; exit 1; }
CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] || { echo "error: claude not found in PATH"; exit 1; }

mkdir -p "$BASE/bin" "$BASE/queue" "$BASE/logs/sessions" "$BASE/logs/dead" "$BASE/logs/done"
cp "$SRC/bin/"*.py "$BASE/bin/"
chmod +x "$BASE/bin/"*.py

# Write config (preserve existing overrides, always refresh claude_bin).
"$PY" - "$CLAUDE_BIN" "$BASE" <<'PYEOF'
import json, os, sys
claude_bin, base = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(base, "bin"))
from autocontinue_common import DEFAULT_CONFIG
path = os.path.join(base, "config.json")
cfg = dict(DEFAULT_CONFIG)
if os.path.exists(path):
    try:
        with open(path) as f:
            cfg.update(json.load(f))
    except ValueError:
        pass
cfg["claude_bin"] = claude_bin
with open(path, "w") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("config written:", path)
PYEOF

# Register the StopFailure hook (idempotent; backs up settings.json first).
[ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bak.autocontinue"
"$PY" - "$HOOK_CMD" "$SETTINGS" <<'PYEOF'
import json, os, sys
hook_cmd, path = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
hooks = data.setdefault("hooks", {})
matchers = hooks.setdefault("StopFailure", [])
matchers[:] = [
    m for m in matchers
    if not any("autocontinue" in h.get("command", "") for h in m.get("hooks", []))
]
matchers.append({
    "matcher": "rate_limit",
    "hooks": [{"type": "command", "command": hook_cmd, "timeout": 30}],
})
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("hook registered in", path)
PYEOF

# Install and (re)load the launchd agent.
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$BASE/bin/autocontinue_checker.py</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$BASE/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$BASE/logs/launchd.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "launchd agent loaded: $LABEL (checks every 5 min)"
echo "done. queue: $BASE/queue  logs: $BASE/logs"

# Default resume_mode is "session" (headless). Optional "inject" mode types the
# resume prompt straight back into the original kitty window; it needs kitty
# remote control, which this installer can't enable for you.
if [ "$(uname)" = "Darwin" ] && [ -d "/Applications/kitty.app" ]; then
  echo
  echo "tip: to use resume_mode \"inject\" (resume visibly in your kitty window),"
  echo "     add to ~/.config/kitty/kitty.conf, then fully restart kitty:"
  echo "         allow_remote_control socket-only"
  echo "         listen_on unix:/tmp/kitty"
  echo "     and set \"resume_mode\": \"inject\" in $BASE/config.json"
  echo "     (only windows opened after the restart can be injected into;"
  echo "      otherwise autocontinue falls back to a headless session resume)."
fi
