#!/usr/bin/env bash
# Prints where this project's handoffs live, and manages the optional in-tree
# symlink that surfaces them inside the project.
#
#   handoff-path.sh                  -> the project's handoff directory
#   handoff-path.sh --commit <draft> -> scan <draft> for secrets, then file it as
#                                       this project's newest handoff and print
#                                       the path. Refuses on a hit (exit 3) and
#                                       writes nothing. This is how a handoff
#                                       gets saved. Removes the draft on success.
#   handoff-path.sh --scan <file>    -> report secrets without writing anything
#   handoff-path.sh --latest         -> most recent existing handoff, or nothing
#   handoff-path.sh --new            -> UNSCANNED path for a new handoff file.
#                                       Escape hatch only; prefer --commit, which
#                                       cannot be saved past a secret.
#   handoff-path.sh --link           -> expose the directory as <project>/.claude/handoff
#   handoff-path.sh --unlink         -> remove that symlink; files are untouched
#   handoff-path.sh --link-status    -> report whether this project is linked
#
# Options:
#   --dir <path>   treat <path> as the project. Default: $HANDOFF_PROJECT_DIR,
#                  else $CLAUDE_PROJECT_DIR, else the enclosing repo root, else $PWD
#   --keep <n>     how many handoffs to retain (default 5)
#
# Files always live in ~/.claude/handoff/<project>-<hash>/. The symlink is only
# a view onto them, so `git clean -xdf` can delete the link but not the content.
set -uo pipefail

# shellcheck source=handoff-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/handoff-lib.sh"

mode="dir"
project=""
draft=""
keep=5

while [ $# -gt 0 ]; do
  case "$1" in
    --new)         mode="new" ;;
    --commit)      mode="commit"; draft="${2:-}"; shift ;;
    --scan)        mode="scan";   draft="${2:-}"; shift ;;
    --latest)      mode="latest" ;;
    --link)        mode="link" ;;
    --unlink)      mode="unlink" ;;
    --link-status) mode="link-status" ;;
    --dir)         project="${2:-}"; shift ;;
    --keep)        keep="${2:-5}"; shift ;;
    -h|--help)     sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

[ -z "$project" ] && project="$(handoff_project_dir_local)"
project="${project%/}"
store="$(handoff_store "$project")"
link="$(handoff_link_path "$project")"

# Report the in-tree path when this project is linked, the real one otherwise.
resolve_base() {
  local _swallow   # handoff_repair_link prints ok|repaired; we only want its status
  _swallow="$(handoff_repair_link "$project" 2>/dev/null)"
  case "$?" in
    0) printf '%s' "$link" ;;
    2) echo "warning: $link exists and is not our symlink — using $store" >&2
       printf '%s' "$store" ;;
    *) printf '%s' "$store" ;;
  esac
}

case "$mode" in
  dir)
    mkdir -p "$store" || exit 1
    printf '%s\n' "$(resolve_base)"
    ;;

  latest)
    newest="$(ls -t "$store"/claude-handoff-*.md 2>/dev/null | head -n1)"
    [ -n "$newest" ] && printf '%s/%s\n' "$(resolve_base)" "${newest##*/}"
    ;;

  new)
    mkdir -p "$store" || exit 1
    handoff_prune "$store" "$keep"
    echo "note: --new bypasses the secret scan; use --commit <draft> to file a handoff." >&2
    printf '%s/claude-handoff-%s.md\n' "$(resolve_base)" "$(date '+%Y-%m-%d-%H%M')"
    ;;

  scan)
    if [ -z "$draft" ]; then echo "--scan needs a file" >&2; exit 2; fi
    report="$(handoff_scan_secrets "$draft")"
    case "$?" in
      0) echo "clean: no secrets found in $draft" ;;
      1) printf '%s\n' "$report" >&2; exit 1 ;;
      *) echo "cannot read: $draft" >&2; exit 1 ;;
    esac
    ;;

  commit)
    if [ -z "$draft" ]; then echo "--commit needs a draft file" >&2; exit 2; fi
    if [ ! -f "$draft" ]; then echo "no such draft: $draft" >&2; exit 1; fi
    if [ ! -s "$draft" ]; then echo "draft is empty: $draft" >&2; exit 1; fi

    # The gate. Nothing is written while a hit stands.
    report="$(handoff_scan_secrets "$draft")"
    case "$?" in
      0) ;;
      1) printf '%s\n' "$report" >&2
         echo "refusing to file this handoff — the draft looks like it contains a secret." >&2
         echo "Redact the line(s) above in $draft, then re-run --commit. Lines marked 'likely' may be false positives; read them before deciding." >&2
         exit 3 ;;
      *) echo "cannot read draft: $draft" >&2; exit 1 ;;
    esac

    mkdir -p "$store" || exit 1
    handoff_prune "$store" "$keep"
    # Minute-resolution names collide if two handoffs are filed in the same
    # minute. --commit consumes the draft, so never silently overwrite.
    base="$(resolve_base)/claude-handoff-$(date '+%Y-%m-%d-%H%M')"
    dest="$base.md"
    n=2
    while [ -e "$dest" ]; do dest="$base-$n.md"; n=$((n + 1)); done

    hdr='<!-- HIGHLY SENSITIVE. Do not share this file. -->'
    if [ "$(head -n1 "$draft")" = "$hdr" ]; then
      cp "$draft" "$dest" || exit 1
    else
      { printf '%s\n' "$hdr"; cat "$draft"; } > "$dest" || exit 1
      echo "note: prepended the sensitivity header, which the draft was missing." >&2
    fi
    chmod 600 "$dest" 2>/dev/null || true
    rm -f "$draft"
    printf '%s\n' "$dest"
    ;;

  link)
    if [ ! -d "$project" ]; then
      echo "not a directory: $project" >&2; exit 1
    fi
    if [ -e "$link" ] && [ ! -L "$link" ]; then
      echo "refusing to replace $link — it already exists and is not a symlink." >&2
      echo "Move or remove it yourself, then re-run --link." >&2
      exit 1
    fi
    mkdir -p "$store" "$(dirname "$link")" || exit 1

    # Ignore it before creating it, so it is never briefly visible to git.
    in_repo=0
    if handoff_git_common_dir "$project" >/dev/null 2>&1; then
      in_repo=1
      handoff_add_exclude "$project" || {
        echo "could not write to .git/info/exclude" >&2; exit 1; }
    fi

    ln -sfn "$store" "$link" || { echo "could not create $link" >&2; exit 1; }

    # Verify rather than assume: global gitignore, nested rules and negation
    # patterns can all defeat the exclude we just wrote.
    if [ "$in_repo" -eq 1 ] && ! git -C "$project" check-ignore -q "$link" 2>/dev/null; then
      rm -f "$link"
      echo "$link would NOT be ignored by git despite the exclude rule." >&2
      echo "Check for a negation pattern (e.g. !.claude/**) in .gitignore or your global ignore file. Symlink removed; nothing was changed." >&2
      exit 1
    fi

    printf '%s\n' "$project" > "$store/.link"
    echo "linked: $link -> $store"
    if [ "$in_repo" -eq 1 ]; then
      echo "ignored via $(handoff_git_common_dir "$project")/info/exclude (verified)"
    else
      echo "not a git repo — no ignore rule needed"
    fi
    ;;

  unlink)
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$store" ]; then
      rm -f "$link" && echo "removed $link"
    else
      echo "no handoff symlink at $link"
    fi
    rm -f "$store/.link"
    echo "handoffs remain in $store"
    ;;

  link-status)
    if [ ! -f "$store/.link" ]; then
      echo "not linked (files in $store)"
    elif [ -L "$link" ] && [ "$(readlink "$link")" = "$store" ]; then
      echo "linked: $link -> $store"
    elif [ -e "$link" ]; then
      echo "blocked: $link exists but is not our symlink (files in $store)"
    else
      echo "link missing — run --link to restore it (files in $store)"
    fi
    ;;
esac
