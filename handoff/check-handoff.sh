#!/usr/bin/env bash
# SessionStart hook for the handoff skill.
# Detects the most recent handoff file in the OS temp dir and tells the agent
# to offer a resume. On exit 0, stdout is added to the session context.
set -uo pipefail

tmp="${TMPDIR:-/tmp}"
latest="$(ls -t "${tmp%/}"/claude-handoff-*.md 2>/dev/null | head -n1 || true)"
[ -z "${latest:-}" ] && exit 0

goal="$(grep -m1 '^# ' "$latest" 2>/dev/null | sed -E 's/^# +//; s/^Handoff — *//')"
when="$(date -r "$latest" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)"

echo "[handoff skill] A handoff file from a previous session was found: ${latest} (saved ${when}). Goal: ${goal:-unknown}."
echo "Tell the user a handoff was found and ask whether to resume. Only read or act on the file if they confirm; then follow the handoff skill's Resume Flow."
