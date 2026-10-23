#!/usr/bin/env bash
# SessionStart hook for the handoff skill. Offers this project's newest handoff
# once: after `handoff-path.sh --resume` it stays quiet until a newer one is
# filed. On exit 0, stdout is added to the session context.
set -uo pipefail

# shellcheck source=handoff-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/handoff-lib.sh"

payload=""
[ -t 0 ] || payload="$(cat 2>/dev/null)"

# Only into a fresh context. After a compaction or `claude --resume` the
# conversation already carries its state, and an offer would point the agent
# back at a handoff it may have just written. Checked here so it holds whatever
# matcher the hook was registered with.
case "$(handoff_json_field "$payload" source)" in
  compact|resume) exit 0 ;;
esac

project="$(handoff_project_dir "$(handoff_json_field "$payload" cwd)")"
store="$(handoff_store "$project")"

# If this project opted into the in-tree symlink, put it back should something
# have removed it (`git clean -xdf` is the usual culprit). Silent by design —
# only an obstruction is worth a line of session context.
handoff_repair_link "$project" >/dev/null 2>&1
case "$?" in
  2) echo "[handoff] $(handoff_link_path "$project") is blocked by a real file or directory; handoffs are still in ${store}." ;;
  3) echo "[handoff] Could not restore the symlink $(handoff_link_path "$project"); handoffs are still in ${store}." ;;
esac

latest="$(handoff_latest "$store")"
[ -n "$latest" ] || exit 0
# Already resumed: handoff-path.sh --resume records the name it handed out.
[ "$(cat "$store/.resumed" 2>/dev/null)" = "${latest##*/}" ] && exit 0

goal="$(grep -m1 '^# ' "$latest" 2>/dev/null | sed -E 's/^# +//; s/^Handoff — *//')"
when="$(date -r "$latest" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)"
echo "[handoff] Unresumed handoff for this project (saved ${when}): ${goal:-no goal line}."
echo "Ask the user whether to resume it. Only if they confirm, follow the handoff skill's Resume steps."
exit 0
