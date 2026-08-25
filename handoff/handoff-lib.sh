#!/usr/bin/env bash
# Shared helpers for the handoff skill. Source this; don't run it.
#
# Handoff files always LIVE per project under:
#
#   ~/.claude/handoff/<basename>-<8-char path hash>/claude-handoff-<YYYY-MM-DD-HHMM>.md
#
# The basename keeps the directory recognizable; the hash keeps it unique
# (two different paths ending in the same basename get different dirs).
#
# A project can additionally expose that directory in-tree as the symlink
# <project>/.claude/handoff (see handoff-path.sh --link). The files stay in
# $HOME, so `git clean -xdf` can only take out the symlink, and a stray commit
# would capture a path string rather than the handoff itself.

handoff_root() {
  printf '%s' "${CLAUDE_HANDOFF_ROOT:-$HOME/.claude/handoff}"
}

# Resolve the project this session belongs to.
# Order: $CLAUDE_PROJECT_DIR -> "cwd" from the hook's JSON payload on stdin -> $PWD.
handoff_project_dir() {
  local dir="${CLAUDE_PROJECT_DIR:-}"
  if [ -z "$dir" ] && [ ! -t 0 ]; then
    dir="$(cat 2>/dev/null \
      | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n1)"
  fi
  [ -z "$dir" ] && dir="$PWD"
  printf '%s' "$dir"
}

handoff_slug() {
  local dir="$1" base hash
  base="${dir%/}"
  base="${base##*/}"
  # Minimal sanitizing only — non-ASCII (e.g. CJK) directory names stay readable.
  base="${base//[\/\\:\*\?\"<>| ]/-}"
  [ -z "$base" ] && base="project"
  hash="$(printf '%s' "${dir%/}" | { md5 -q 2>/dev/null || md5sum | cut -d' ' -f1; })"
  printf '%s-%s' "$base" "${hash:0:8}"
}

# Where this project's handoffs physically live. Does not create it.
handoff_store() {
  local dir="${1:-}"
  [ -z "$dir" ] && dir="$(handoff_project_dir)"
  printf '%s/%s' "$(handoff_root)" "$(handoff_slug "$dir")"
}

# In-tree symlink path, and the marker recording that this project opted in.
handoff_link_path()   { printf '%s/.claude/handoff' "${1%/}"; }
handoff_link_marker() { printf '%s/.link' "$(handoff_store "$1")"; }

# The repo's shared git dir (one per repo, common to all worktrees), or nothing.
handoff_git_common_dir() {
  local dir="$1" out
  out="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
    || out="$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null)" \
    || return 1
  [ -z "$out" ] && return 1
  case "$out" in /*) ;; *) out="${dir%/}/$out" ;; esac
  printf '%s' "$out"
}

# Append the ignore rule to .git/info/exclude — local to this clone, shared by
# every worktree, and never showing up in anyone's diff. No-op outside a repo.
handoff_add_exclude() {
  local dir="$1" common exclude rule='/.claude/handoff'
  common="$(handoff_git_common_dir "$dir")" || return 1
  exclude="${common%/}/info/exclude"
  mkdir -p "$(dirname "$exclude")" || return 1
  if [ -f "$exclude" ] && grep -qxF "$rule" "$exclude" 2>/dev/null; then
    return 0
  fi
  printf '%s\n' "$rule" >> "$exclude" || return 1
}

# Restore the symlink if this project opted in and it went missing (the usual
# cause is `git clean -xdf`). Prints ok | repaired; stays quiet otherwise.
#   0 link is in place   1 not opted in   2 something real is in the way
#   3 could not create   4 marker points at a project that no longer exists
handoff_repair_link() {
  local project="$1" store link
  store="$(handoff_store "$project")"
  [ -f "$store/.link" ] || return 1
  [ -d "${project%/}" ] || return 4
  link="$(handoff_link_path "$project")"
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$store" ]; then
    printf 'ok'
    return 0
  fi
  # Never clobber a real directory or file sitting at that path.
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    return 2
  fi
  mkdir -p "$store" "$(dirname "$link")" || return 3
  ln -sfn "$store" "$link" || return 3
  printf 'repaired'
  return 0
}
