#!/usr/bin/env bash
# Install write-register: copy the skill, install the output style, make it the
# global default, and retire the two skills it supersedes (caveman, stop-slop).
# Safe to re-run after updates (idempotent).
#
# The output style is the always-on half. Claude Code appends it to the system
# prompt every turn, so register selection happens without being asked; the skill
# body (tell lists, check/fix modes) stays on demand.
#
# keep-coding-instructions: true in the style's frontmatter preserves Claude Code's
# built-in software engineering instructions, which a custom style drops by default.
#
# caveman and stop-slop are backed up, not deleted. uninstall.sh puts them back.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
SKILL_DIR="$CLAUDE_DIR/skills/write-register"
STYLES_DIR="$CLAUDE_DIR/output-styles"
STYLE_DEST="$STYLES_DIR/write-register.md"
SETTINGS="$CLAUDE_DIR/settings.json"
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
BACKUP_DIR="$CLAUDE_DIR/write-register-superseded"
PY=/usr/bin/python3

[ -x "$PY" ] || { echo "error: $PY not found (install Xcode Command Line Tools)"; exit 1; }

# 1. Skill files.
mkdir -p "$SKILL_DIR/references"
cp "$SRC/SKILL.md" "$SKILL_DIR/"
cp "$SRC/references/tells-zh.md" "$SRC/references/tells-en.md" "$SKILL_DIR/references/"
echo "installed: $SKILL_DIR/{SKILL.md,references/}"

# 2. Output style.
mkdir -p "$STYLES_DIR"
cp "$SRC/write-register-style.md" "$STYLE_DEST"
echo "installed: $STYLE_DEST"

# 3. Make it the global default. /config writes the project-local
#    .claude/settings.local.json, which still wins per-project if you set it there.
[ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bak.write-register"
"$PY" - "$SETTINGS" <<'PYEOF'
import json, os, sys
path = sys.argv[1]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
prev = data.get("outputStyle")
data["outputStyle"] = "write-register"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
if prev == "write-register":
    print("outputStyle already set to write-register in", path)
else:
    print(f"outputStyle: {prev!r} -> 'write-register' in {path}")
PYEOF

# 4. Migrate off the older CLAUDE.md @-include layout, if this box has it.
LEGACY_POLICY="$CLAUDE_DIR/write-register-policy.md"
if [ -f "$CLAUDE_MD" ] && grep -qF '@write-register-policy.md' "$CLAUDE_MD"; then
  cp "$CLAUDE_MD" "$CLAUDE_MD.bak.write-register"
  /usr/bin/sed -e '/^@write-register-policy\.md$/d' -e '/^## 輸出語域$/d' \
    "$CLAUDE_MD.bak.write-register" | "$PY" -c \
    'import sys; sys.stdout.write(sys.stdin.read().rstrip("\n") + "\n")' > "$CLAUDE_MD"
  echo "migrated: dropped @write-register-policy.md from $CLAUDE_MD"
fi
[ -f "$LEGACY_POLICY" ] && rm -f "$LEGACY_POLICY" && echo "migrated: removed $LEGACY_POLICY"

# 5. Retire the superseded skills. Their trigger phrases overlap write-register's
#    ("be brief", "簡短一點"), so leaving them installed reproduces the conflict
#    this skill exists to remove.
mkdir -p "$BACKUP_DIR"
for old in caveman stop-slop; do
  if [ -d "$CLAUDE_DIR/skills/$old" ]; then
    rm -rf "${BACKUP_DIR:?}/$old"
    mv "$CLAUDE_DIR/skills/$old" "$BACKUP_DIR/$old"
    echo "retired: skills/$old -> $BACKUP_DIR/$old"
  fi
done
rmdir "$BACKUP_DIR" 2>/dev/null || true

echo "done. run /clear or start a new session to load the style into the system prompt."
