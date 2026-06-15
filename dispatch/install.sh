#!/usr/bin/env bash
# Install dispatch's auto-dispatch layer: copy the skill, hook, and policy,
# register the UserPromptSubmit nudge hook, and @-include the policy from
# CLAUDE.md. Safe to re-run after updates (idempotent).
#
# dispatch.py works on demand without this — the auto-dispatch layer is what
# makes Claude evaluate delegation on its own (nudge hook + policy), instead of
# only when you say "/dispatch".
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
SKILL_DIR="$CLAUDE_DIR/skills/dispatch"
HOOKS_DIR="$CLAUDE_DIR/hooks"
HOOK_DEST="$HOOKS_DIR/dispatch-nudge.sh"
POLICY_DEST="$CLAUDE_DIR/dispatch-policy.md"
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
SETTINGS="$CLAUDE_DIR/settings.json"
PY=/usr/bin/python3
HOOK_CMD="bash $HOOK_DEST"

[ -x "$PY" ] || { echo "error: $PY not found (install Xcode Command Line Tools)"; exit 1; }

# 1. Skill files (so re-running also updates the installed skill).
mkdir -p "$SKILL_DIR" "$HOOKS_DIR"
cp "$SRC/dispatch.py" "$SRC/SKILL.md" "$SKILL_DIR/"

# 2. Hook + policy.
cp "$SRC/dispatch-nudge.sh" "$HOOK_DEST"
chmod +x "$HOOK_DEST"
cp "$SRC/dispatch-policy.md" "$POLICY_DEST"
echo "installed: $SKILL_DIR/{dispatch.py,SKILL.md}, $HOOK_DEST, $POLICY_DEST"

# 3. Register the UserPromptSubmit hook (idempotent; back up settings first).
[ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bak.dispatch"
"$PY" - "$HOOK_CMD" "$SETTINGS" <<'PYEOF'
import json, os, sys
hook_cmd, path = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
hooks = data.setdefault("hooks", {})
entries = hooks.setdefault("UserPromptSubmit", [])
# Drop any prior dispatch-nudge registration, then add ours back.
entries[:] = [
    e for e in entries
    if not any("dispatch-nudge" in h.get("command", "") for h in e.get("hooks", []))
]
entries.append({"hooks": [{"type": "command", "command": hook_cmd}]})
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("UserPromptSubmit hook registered in", path)
PYEOF

# 4. Ensure the policy is @-included from CLAUDE.md (idempotent).
if [ -f "$CLAUDE_MD" ] && grep -qF '@dispatch-policy.md' "$CLAUDE_MD"; then
  echo "CLAUDE.md already includes @dispatch-policy.md"
else
  printf '@dispatch-policy.md\n' >> "$CLAUDE_MD"
  echo "added @dispatch-policy.md to $CLAUDE_MD"
fi

echo "done. dispatch auto-dispatch installed — restart Claude Code to load the new hook."
