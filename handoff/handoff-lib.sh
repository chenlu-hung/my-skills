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

# --- secret gate -----------------------------------------------------------
# The scan runs before a draft is filed (handoff-path.sh --commit), because a
# handoff is written at the exact moment context is running out and a checklist
# step is easiest to skip. It only ever blocks; it never edits the draft.
# Rewriting a secret in place would hand the next session a silently altered
# document, which is harder to notice than a refusal.

# Secrets identifiable by shape alone. Case-sensitive: the prefixes are literal.
handoff_secret_shapes() {
  cat <<'EOF'
-----BEGIN [A-Z ]*PRIVATE KEY-----
AGE-SECRET-KEY-1[0-9A-Z]{50,}
sk-ant-[A-Za-z0-9_-]{20,}
sk-[A-Za-z0-9_-]{32,}
gh[pousr]_[A-Za-z0-9]{36}
github_pat_[A-Za-z0-9_]{22,}
glpat-[A-Za-z0-9_-]{20,}
(AKIA|ASIA)[0-9A-Z]{16}
AIza[A-Za-z0-9_-]{35}
xox[abprs]-[A-Za-z0-9-]{10,}
xapp-[0-9]-[A-Za-z0-9-]{10,}
(sk|rk|pk)_(live|test)_[A-Za-z0-9]{16,}
npm_[A-Za-z0-9]{36}
hf_[A-Za-z0-9]{30,}
dop_v1_[a-f0-9]{64}
SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}
eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}
[Bb]earer [A-Za-z0-9._~+/-]{20,}
[a-zA-Z][a-zA-Z0-9+.-]*://[^/[:space:]:@]+:[^/[:space:]@]+@
EOF
}

# A credential-ish name assigned a value that is long enough to be real. The
# assignment is what makes this usable: prose about tokens or passwords is
# everywhere in a handoff, `token = <40 chars>` is not.
handoff_secret_assignments() {
  printf '%s\n' "(api[_-]?key|secret([_-]?(key|token))?|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?secret|passwo?rd|passwd|private[_-]?key|credentials?)[\"']?[[:space:]]*[:=][[:space:]]*[\"']?[^\"'[:space:],;)]{6,}"
}

# Values that look assigned but carry nothing: placeholders, env references,
# masked stubs, plain numbers.
handoff_secret_placeholders() {
  printf '%s\n' "[:=][[:space:]]*[\"']?(<[^>]*>|\{\{?[A-Za-z0-9_. -]+\}?\}|\\\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[Xx]{3,}|\*{3,}|\.{3,}|REDACTED|redacted|REMOVED|TODO|CHANGE_?ME|changeme|your[_-]?[A-Za-z]+|example[A-Za-z0-9_-]*|dummy|placeholder|null|nil|true|false|[0-9]+)[\"']?[[:space:],;)]*\$"
}

# Long runs are masked out of the report: the point is to name the line, not to
# copy the secret into a terminal, a transcript, or the next handoff.
handoff_mask_line() {
  # Inline URL credentials go first: a short password like `hunter2` survives
  # the length rule below, and it is still a password.
  sed -E -e 's|://[^/[:space:]@]*@|://[MASKED]@|g' \
         -e 's/[A-Za-z0-9_+/=-]{12,}/[MASKED]/g'
}

# Scan a file. Prints one `secret|likely <line>: <masked text>` per hit.
#   0 clean   1 hits found   2 unreadable
handoff_scan_secrets() {
  local file="$1" shapes assigns hit_lines=""
  [ -r "$file" ] || return 2

  shapes="$(handoff_secret_shapes | grep -nE -f - -- "$file" 2>/dev/null)"
  assigns="$(handoff_secret_assignments \
    | grep -oinE -f - -- "$file" 2>/dev/null \
    | grep -ivE -f <(handoff_secret_placeholders) 2>/dev/null)"

  if [ -n "$shapes" ]; then
    printf '%s\n' "$shapes" | while IFS= read -r line; do
      printf 'secret %s\n' "$(printf '%s' "$line" | handoff_mask_line)"
    done
    hit_lines="$(printf '%s\n' "$shapes" | cut -d: -f1)"
  fi

  if [ -n "$assigns" ]; then
    printf '%s\n' "$assigns" | while IFS= read -r line; do
      # Skip a line already reported by shape — one hit per line is enough.
      printf '%s\n' "$hit_lines" | grep -qxF "${line%%:*}" && continue
      printf 'likely %s\n' "$(printf '%s' "$line" | handoff_mask_line)"
    done
  fi

  [ -z "$shapes" ] && [ -z "$assigns" ] && return 0
  return 1
}

# Keep the newest $keep handoffs in $store. Shared by --commit and --new so the
# two cannot drift apart.
handoff_prune() {
  local store="$1" keep="${2:-5}"
  [ "$keep" -gt 0 ] 2>/dev/null || return 0
  ls -t "$store"/claude-handoff-*.md 2>/dev/null \
    | tail -n +"$keep" \
    | while IFS= read -r old; do rm -f "$old"; done
}
