#!/usr/bin/env bash
# Install action: copy scripts, write config, drop an `action` wrapper on PATH,
# and load the launchd runner agent. Safe to re-run after updates.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/.claude/action"
PY=/usr/bin/python3
LABEL=com.luhung.action
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$PY" ] || { echo "error: $PY not found (install Xcode Command Line Tools)"; exit 1; }
CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] || { echo "error: claude not found in PATH"; exit 1; }

mkdir -p "$BASE/bin" "$BASE/queue" "$BASE/logs/runs" "$BASE/logs/dead" "$BASE/logs/done"
cp "$SRC/bin/"*.py "$BASE/bin/"
chmod +x "$BASE/bin/"*.py

# Write config (preserve existing overrides, always refresh claude_bin).
"$PY" - "$CLAUDE_BIN" "$BASE" <<'PYEOF'
import json, os, sys
claude_bin, base = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(base, "bin"))
from action_common import DEFAULT_CONFIG
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

# Drop an `action` wrapper into ~/.local/bin if that dir is on PATH.
if [ -d "$HOME/.local/bin" ]; then
  WRAPPER="$HOME/.local/bin/action"
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$PY" "$BASE/bin/action.py" "\$@"
EOF
  chmod +x "$WRAPPER"
  echo "wrapper installed: $WRAPPER"
else
  echo "note: ~/.local/bin not found; call it as: $PY $BASE/bin/action.py ..."
fi

# Install and (re)load the launchd agent that sweeps the queue every 5 min.
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
    <string>$BASE/bin/action_runner.py</string>
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
echo "launchd agent loaded: $LABEL (sweeps queue every 5 min)"
echo "done. queue: $BASE/queue  logs: $BASE/logs"
echo
echo "tip: jobs run with --dangerously-skip-permissions by default so they can"
echo "     run unattended. For full overnight autonomy, also install ../autocontinue"
echo "     so a job that hits a usage limit mid-run is resumed after reset."
