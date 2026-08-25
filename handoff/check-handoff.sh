#!/usr/bin/env bash
# SessionStart hook for the handoff skill.
# Detects the most recent handoff *for this project* and tells the agent to
# offer a resume. On exit 0, stdout is added to the session context.
set -uo pipefail

# shellcheck source=handoff-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/handoff-lib.sh"

project="$(handoff_project_dir)"
store="$(handoff_store "$project")"

# If this project opted into the in-tree symlink, put it back should something
# have removed it (`git clean -xdf` is the usual culprit). Silent by design —
# only an obstruction is worth a line of session context.
link_note=""
repair="$(handoff_repair_link "$project" 2>/dev/null)"
case "$?" in
  2) link_note="[handoff skill] $(handoff_link_path "$project") is blocked by a real file or directory, so the in-tree view is unavailable; handoffs are still at ${store}." ;;
  3) link_note="[handoff skill] Could not restore the handoff symlink at $(handoff_link_path "$project"); handoffs are still at ${store}." ;;
esac
[ -n "$link_note" ] && echo "$link_note"

latest="$(ls -t "$store"/claude-handoff-*.md 2>/dev/null | head -n1 || true)"

if [ -n "${latest:-}" ]; then
  goal="$(grep -m1 '^# ' "$latest" 2>/dev/null | sed -E 's/^# +//; s/^Handoff — *//')"
  when="$(date -r "$latest" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)"

  # Report the in-tree path when the symlink is live — that is where the user
  # expects to find it.
  shown="$latest"
  if [ "$repair" = "ok" ] || [ "$repair" = "repaired" ]; then
    shown="$(handoff_link_path "$project")/${latest##*/}"
  fi

  echo "[handoff skill] A handoff for this project (${project}) was found: ${shown} (saved ${when}). Goal: ${goal:-unknown}."
  echo "Tell the user a handoff was found and ask whether to resume. Only read or act on the file if they confirm; then follow the handoff skill's Resume Flow."
  exit 0
fi

# Nothing for this project. Flag pre-scoping handoffs left in the OS temp dir so
# they can be filed or discarded; drop this block once none remain.
tmp="${TMPDIR:-/tmp}"
legacy_count="$(ls -1 "${tmp%/}"/claude-handoff-*.md 2>/dev/null | wc -l | tr -d ' ')"
if [ "${legacy_count:-0}" -gt 0 ]; then
  echo "[handoff skill] No handoff for this project. ${legacy_count} handoff file(s) remain in the old unscoped location (${tmp%/}/claude-handoff-*.md) and may belong to a different project."
  echo "Mention this only if the user asks about resuming; do not read those files unprompted."
fi
exit 0
