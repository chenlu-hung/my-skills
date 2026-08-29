#!/usr/bin/env bash
# Install chatgpt-bridge into every agent CLI on this machine that loads skills,
# and put `chatgpt-ask` on PATH.
#
# All three read the same format -- <name>/SKILL.md with YAML frontmatter -- and
# differ only in where they look:
#
#   Claude Code  ~/.claude/skills/<name>/
#   Codex        ~/.codex/skills/<name>/
#   opencode     ~/.config/opencode/skills/<name>/
#
# opencode can also auto-load ~/.claude/skills, but only while
# OPENCODE_DISABLE_EXTERNAL_SKILLS is unset, so installing into its own directory
# is what makes the skill present either way.
#
# Everything is a symlink back to this directory: one SKILL.md, one script, three
# harnesses. Copies drift, and this repo has already paid for that once.
#
# Idempotent -- re-run it after an update.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
NAME=chatgpt-bridge

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

chmod +x "$SRC/chatgpt_ask.py"
mkdir -p "$HOME/.local/bin"
link "$SRC/chatgpt_ask.py" "$HOME/.local/bin/chatgpt-ask"

# Only where the harness is actually installed. Creating ~/.codex on a machine
# without Codex would leave litter that looks like configuration.
found=0
for skills in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.config/opencode/skills"; do
  home="$(dirname "$skills")"
  if [ ! -d "$home" ]; then
    echo "absent:    $home -- skipping"
    continue
  fi
  mkdir -p "$skills"
  link "$SRC" "$skills/$NAME"
  found=1
done
[ "$found" = 1 ] || echo "warning: no agent CLI found to install into" >&2

# Both of these fail before the script's own code runs, so they cannot be
# reported from inside it.
command -v uv >/dev/null || echo "warning: uv is not on PATH -- the shebang runs the script through it" >&2
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "warning: $HOME/.local/bin is not on PATH -- add it, or call chatgpt_ask.py by full path" >&2 ;;
esac

echo
echo "check what the current host allows:  chatgpt-ask --doctor"
