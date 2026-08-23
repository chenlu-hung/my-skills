#!/usr/bin/env bash
# Remove write-register, revert the output style to Default, and restore the skills
# it superseded.
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"
SKILL_DIR="$CLAUDE_DIR/skills/write-register"
STYLE_DEST="$CLAUDE_DIR/output-styles/write-register.md"
SETTINGS="$CLAUDE_DIR/settings.json"
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
BACKUP_DIR="$CLAUDE_DIR/write-register-superseded"
PY=/usr/bin/python3

rm -rf "$SKILL_DIR"
rm -f "$STYLE_DEST"
echo "removed: $SKILL_DIR, $STYLE_DEST"

# Unset outputStyle, but only if it is still ours — never clobber a style you picked
# afterwards.
if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak.write-register"
  "$PY" - "$SETTINGS" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
if data.get("outputStyle") == "write-register":
    del data["outputStyle"]
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("outputStyle removed from", path, "(back to Default)")
else:
    print("outputStyle is", repr(data.get("outputStyle")), "— left alone")
PYEOF
fi

# Legacy @-include layout, if an older install left it behind.
if [ -f "$CLAUDE_MD" ] && grep -qF '@write-register-policy.md' "$CLAUDE_MD"; then
  cp "$CLAUDE_MD" "$CLAUDE_MD.bak.write-register"
  /usr/bin/sed -e '/^@write-register-policy\.md$/d' -e '/^## 輸出語域$/d' \
    "$CLAUDE_MD.bak.write-register" | "$PY" -c \
    'import sys; sys.stdout.write(sys.stdin.read().rstrip("\n") + "\n")' > "$CLAUDE_MD"
  echo "removed @write-register-policy.md from $CLAUDE_MD"
fi
rm -f "$CLAUDE_DIR/write-register-policy.md"

# Put caveman / stop-slop back.
if [ -d "$BACKUP_DIR" ]; then
  for old in "$BACKUP_DIR"/*; do
    [ -e "$old" ] || continue
    name="$(basename "$old")"
    if [ -e "$CLAUDE_DIR/skills/$name" ]; then
      echo "skipped restore: skills/$name already exists"
    else
      mv "$old" "$CLAUDE_DIR/skills/$name"
      echo "restored: skills/$name"
    fi
  done
  rmdir "$BACKUP_DIR" 2>/dev/null || true
fi

echo "done. run /clear or start a new session to drop the style from the system prompt."
