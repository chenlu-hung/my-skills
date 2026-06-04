# my-skills

Personal collection of [Claude Code](https://claude.com/claude-code) Agent Skills.

| Skill | Purpose |
|---|---|
| [`handoff`](./handoff) | Context transfer between AI coding sessions — creates a compact handoff doc so a fresh agent can resume. Includes a SessionStart hook (`check-handoff.sh`) that auto-detects handoff files. Integrates with `project-map` (below) to keep docs lean and cut resume-time exploration. |
| [`grill-me`](./grill-me) | Stress-tests a plan or design by interviewing you relentlessly until the decision tree is resolved. |
| [`caveman`](./caveman) | Ultra-compressed communication mode — cuts token usage ~75% while keeping technical accuracy. |
| [`project-map`](./project-map) | Builds a committed, on-demand `.projectmap/` index (ctags symbols + short module summaries) so agents remember the codebase and grep the map instead of re-scanning the repo. Includes `build-map.py`; requires `universal-ctags`. |

## How `handoff` + `project-map` compose

They attack the two halves of resume cost. `project-map` carries the *codebase structure* (where things are); `handoff` carries the *session state* (decisions, dead ends, next steps). During a handoff, the skill runs `project-map`'s read-only `status` check:

- **On resume** — if the map is missing or stale, it offers to `build`/`update` so the fresh session reads the map instead of re-scanning the repo.
- **On create** — it records a rebuild as a next step rather than spending build tokens at wrap-up.

A current map also lets handoff docs link `.projectmap/ARCHITECTURE.md` instead of re-describing structure, so the doc the next session reads back stays small.

## Install

Copy any skill folder into `~/.claude/skills/`:

```sh
cp -R handoff ~/.claude/skills/
```

> **Note**: `handoff`'s SessionStart hook must be registered separately in `~/.claude/settings.json` and references `~/.claude/skills/handoff/check-handoff.sh`.
