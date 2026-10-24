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
# `chatgpt-ask` itself needs a Python with `websockets`. Where conda is installed
# it gets a `chatgpt-bridge` env and `chatgpt-ask` is a two-line launcher that
# runs this directory's script with that env's Python -- still no copy of the
# script. Otherwise it is a symlink, and the script's PEP 723 shebang has `uv`
# resolve the dependency. CHATGPT_BRIDGE_RUNNER=conda|uv overrides the choice.
#
# Idempotent -- re-run it after an update.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
NAME=chatgpt-bridge
ENV_NAME=chatgpt-bridge
LAUNCHER_MARK="# chatgpt-bridge launcher (written by install.sh)"

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

conda_bin() {
  if [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then echo "$CONDA_EXE"; return; fi
  command -v conda 2>/dev/null && return
  for c in /opt/miniconda3/bin/conda /opt/anaconda3/bin/conda "$HOME/miniconda3/bin/conda"; do
    [ -x "$c" ] && { echo "$c"; return; }
  done
  return 1
}

runner="${CHATGPT_BRIDGE_RUNNER:-}"
if [ -z "$runner" ]; then
  if conda_bin >/dev/null; then runner=conda; else runner=uv; fi
fi

chmod +x "$SRC/chatgpt_ask.py"
mkdir -p "$HOME/.local/bin"
dest="$HOME/.local/bin/chatgpt-ask"

if [ "$runner" = conda ]; then
  CONDA="$(conda_bin)" || { echo "error: CHATGPT_BRIDGE_RUNNER=conda but no conda found" >&2; exit 1; }
  py="$("$CONDA" run -n "$ENV_NAME" python -c 'import sys, websockets; print(sys.executable)' 2>/dev/null || true)"
  if [ -z "$py" ]; then
    if "$CONDA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
      "$CONDA" install -y -n "$ENV_NAME" -c conda-forge websockets
    else
      "$CONDA" create -y -n "$ENV_NAME" -c conda-forge "python>=3.10" websockets
    fi
    py="$("$CONDA" run -n "$ENV_NAME" python -c 'import sys; print(sys.executable)')"
  fi
  if [ -e "$dest" ] && [ ! -L "$dest" ] && ! grep -qF "$LAUNCHER_MARK" "$dest"; then
    echo "skipped:   $dest exists and was not written by this installer -- remove it by hand" >&2
  else
    rm -f "$dest"
    printf '#!/bin/sh\n%s\nexec "%s" "%s" "$@"\n' "$LAUNCHER_MARK" "$py" "$SRC/chatgpt_ask.py" > "$dest"
    chmod +x "$dest"
    echo "installed: $dest -> $py $SRC/chatgpt_ask.py"
  fi
else
  if [ -e "$dest" ] && [ ! -L "$dest" ] && grep -qF "$LAUNCHER_MARK" "$dest"; then
    rm "$dest"  # a launcher from an earlier conda install; the symlink replaces it
  fi
  link "$SRC/chatgpt_ask.py" "$dest"
fi

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
if [ "$runner" = uv ]; then
  command -v uv >/dev/null || echo "warning: uv is not on PATH -- the shebang runs the script through it" >&2
fi
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "warning: $HOME/.local/bin is not on PATH -- add it, or call chatgpt_ask.py by full path" >&2 ;;
esac

echo
echo "check what the current host allows:  chatgpt-ask --doctor"
