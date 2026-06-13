# my-skills

Personal collection of [Claude Code](https://claude.com/claude-code) Agent Skills.

| Skill | Purpose |
|---|---|
| [`handoff`](./handoff) | Context transfer between AI coding sessions — creates a compact handoff doc so a fresh agent can resume. Includes a SessionStart hook (`check-handoff.sh`) that auto-detects handoff files. Integrates with `project-map` (below) to keep docs lean and cut resume-time exploration. |
| [`grill-me`](./grill-me) | Stress-tests a plan or design by interviewing you relentlessly until the decision tree is resolved. |
| [`caveman`](./caveman) | Ultra-compressed communication mode — cuts token usage ~75% while keeping technical accuracy. |
| [`project-map`](./project-map) | Builds a committed, on-demand `.projectmap/` index (ctags symbols + short module summaries) so agents remember the codebase and grep the map instead of re-scanning the repo. Includes `build-map.py`; requires `universal-ctags`. |
| [`llm-council`](./llm-council) | Convenes a multi-model council — Codex (ChatGPT sub), Gemini (Antigravity `agy`), Claude (`claude -p`), and DeepSeek (opencode, free) — to answer a question, cross-review each other anonymously, then this session chairs the synthesis. Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council); every member runs through its **own subscription/sign-in CLI**, no API keys. Includes `council.py` (parallel dispatch). |
| [`dispatch`](./dispatch) | Delegates coding tasks to external agent CLIs as writable **worker subagents** — Codex (ChatGPT sub), Gemini (Antigravity `agy`), and DeepSeek (opencode, free). Splits work into self-contained briefs, runs workers in parallel (optionally in isolated git worktrees), then this session reviews and merges. Each worker runs through its **own subscription/sign-in CLI**, no API keys. Includes `dispatch.py`. |

## Tools

Not a skill, but lives here too:

| Tool | Purpose |
|---|---|
| [`autocontinue`](./autocontinue) | Auto-resumes a Claude Code session after a usage-limit reset. A `StopFailure` hook queues the interrupted session; a launchd agent (every 5 min) resumes it headlessly once the limit resets, reusing the original permission mode and stopping after a configurable chain limit. Install once via `autocontinue/install.sh` — applies to all sessions. macOS only. |

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
