#!/usr/bin/env bash
# SessionStart hook for the project-map skill. When the session's project has a
# map and some modules changed since its last build/update, it adds one note to
# the session context naming them, so the agent reads those modules' source
# instead of trusting their stale docs. Silent when the map is current, when the
# project was never mapped (no nagging to build), and on any error.
# On exit 0, stdout is added to the session context.
set -uo pipefail

bm="$(dirname "${BASH_SOURCE[0]}")/build-map.py"

payload=""
[ -t 0 ] || payload="$(cat 2>/dev/null)"

# A top-level string field from the hook's JSON payload (sed, not jq: no deps;
# the fields read here hold no escaped quotes).
field() {
  printf '%s' "$payload" | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1
}

# Only into a fresh context: after a compaction or `claude --resume` the
# conversation already knows what it changed. Checked here so it holds whatever
# matcher the hook was registered with.
case "$(field source)" in
  compact|resume) exit 0 ;;
esac

# The nearest map at or above the launch directory, as a physical path — a
# session started in a subdirectory still finds the map at the repo root.
dir="$(cd -P "${CLAUDE_PROJECT_DIR:-$(field cwd)}" 2>/dev/null && pwd -P)" || dir="$(pwd -P)"
while [ ! -f "$dir/.projectmap/manifest.json" ]; do
  [ "$dir" = "/" ] && exit 0
  dir="$(dirname "$dir")"
done

out="$(python3 "$bm" status "$dir" 2>/dev/null)" || exit 0
case "$out" in
  *"/project-map update"*) ;;
  *) exit 0 ;;
esac

# The "  ~ mod (changed)" / "  + mod (new)" / "  - mod (removed)" lines.
mods="$(printf '%s\n' "$out" | sed -n 's/^  [~+-] \(.*\)$/\1/p')"
n="$(printf '%s\n' "$mods" | grep -c .)"
list="$(printf '%s\n' "$mods" | head -n 8 | paste -sd ',' - | sed 's/,/, /g')"
[ "$n" -gt 8 ] && list="$list, …+$((n - 8)) more"

echo "[project-map] The map in ${dir}/.projectmap is behind the code for ${n} module(s): ${list}."
echo "For those modules, read the source instead of trusting their map docs (line numbers, symbols, Used by may be off); every other module's doc is current. If the task touches them, offer \`/project-map update\`."
exit 0
