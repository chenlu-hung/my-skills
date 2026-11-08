#!/usr/bin/env bash
# Install the dicos skill into every agent CLI on this machine that loads skills.
#
# All of them read the same format -- <name>/SKILL.md with YAML frontmatter -- and
# differ only in where they look:
#
#   Claude Code  ~/.claude/skills/<name>/
#   Codex        ~/.codex/skills/<name>/
#   opencode     ~/.config/opencode/skills/<name>/
#   pi           ~/.pi/agent/skills/<name>/
#
# Each is a symlink back to this directory. The link is named `dicos` because the
# skill name must be lowercase and match its directory, and this one is `DiCOS`.
#
# Idempotent -- re-run it after an update.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
NAME=dicos

link() {  # link <target> <linkname>
  local target=$1 dest=$2
  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$target" ]; then
      echo "ok:        $dest"
      return
    fi
    rm "$dest"
  elif [ -e "$dest" ]; then
    echo "skipped:   $dest exists and is not a symlink -- remove it by hand" >&2
    return
  fi
  ln -s "$target" "$dest"
  echo "installed: $dest -> $target"
}

# Only where the harness is actually installed.
found=0
for pair in \
  "$HOME/.claude:$HOME/.claude/skills" \
  "$HOME/.codex:$HOME/.codex/skills" \
  "$HOME/.config/opencode:$HOME/.config/opencode/skills" \
  "$HOME/.pi/agent:$HOME/.pi/agent/skills"; do
  home="${pair%%:*}"
  skills="${pair#*:}"
  if [ ! -d "$home" ]; then
    echo "absent:    $home -- skipping"
    continue
  fi
  mkdir -p "$skills"
  link "$SRC" "$skills/$NAME"
  found=1
done
[ "$found" = 1 ] || echo "warning: no agent CLI found to install into" >&2
