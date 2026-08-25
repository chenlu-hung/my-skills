# my-skills

Personal collection of [Claude Code](https://claude.com/claude-code) Agent Skills.

**Design principle — model-robust by construction.** These skills are written to degrade gracefully on smaller driving models (e.g. Opus instead of Fable): taste-based judgment is replaced with explicit decision tests and thresholds, multi-step workflows carry non-skippable ordered checklists and literal output templates, and anything a script can do deterministically (shuffling/anonymizing council answers, indexing, change detection) lives in a script instead of a prompt.

| Skill | Purpose |
|---|---|
| [`handoff`](./handoff) | Context transfer between AI coding sessions — creates a compact handoff doc so a fresh agent can resume. Handoffs are **scoped per project** under `~/.claude/handoff/<project>-<hash>/`, so several projects in flight never cross-contaminate; a SessionStart hook (`check-handoff.sh`) offers a resume only for the project you opened. `handoff-path.sh --link` optionally surfaces them in-tree as `<project>/.claude/handoff` — a symlink, so `git clean -xdf` costs you the link and not the content, and the hook restores it. Integrates with `project-map` (below) to keep docs lean and cut resume-time exploration. |
| [`grill-me`](./grill-me) | Stress-tests a plan or design by interviewing you relentlessly until the decision tree is resolved. |
| [`FableAdvisor`](./FableAdvisor) | Codifies the Anthropic-recommended architect/implementer split: Fable 5 (the session) runs `grill-me`'s interview, designs the architecture into a self-contained doc, then hands off to an **implementer subagent** (default Sonnet 5; overridable to opus/haiku, e.g. `/fable-advisor opus`) that implements against the doc and consults Fable (via `CONSULT` → SendMessage ruling) when blocked on design; Fable reviews the diff against the doc before closing. Installs as `fable-advisor`. |
| [`write-register`](./write-register) | Picks the writing register per output segment, automatically. One test decides it — *will this text still exist after the conversation ends?* — routing to **CHAT** (dense but not telegraphic: it deletes whole moves like restatement and self-summary, and keeps the grammar), **DOC** (natural, self-contained prose for anything that gets saved, with 中文 and English AI-tell lists), or **CODE** (byte-exact, prose rules off). Supersedes `caveman` and `stop-slop`, which contradicted each other — one wanted fragments, the other complete sentences, and neither knew which text it was looking at. Ships as a Claude Code **output style** (`keep-coding-instructions: true`, so the built-in engineering instructions stay), because a classifier that isn't in the system prompt can't classify; the tell lists and `check`/`fix` modes stay on demand in the skill. |
| [`project-map`](./project-map) | Builds a committed, on-demand `.projectmap/` index (ctags symbols + short module summaries) so agents remember the codebase and grep the map instead of re-scanning the repo. Includes `build-map.py`; requires `universal-ctags`. |
| [`llm-council`](./llm-council) | Convenes a multi-model council — Codex (ChatGPT sub), Gemini (Antigravity `agy`), and Claude (`claude -p`) — to answer a question, cross-review each other anonymously, then this session chairs the synthesis. Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council); every member runs through its **own subscription/sign-in CLI**, no API keys. Includes `council.py` (parallel dispatch) and `test_council.py` (regression tests for the read-only/borrowed-directory guarantees). |
| [`review-me`](./review-me) | Sends *your own* plan or diff to independent external models (Codex, Gemini, optionally a fresh Claude) to find what you **missed** — the characteristic failure of one strong model working alone. Reviewers run **inside the real repo, read-only** (via `council.py --workdir`), because hand-picked context would just reproduce your blind spot. Four modes: `plan` (pre-code omissions), `gap` (post-code omissions, no debate — an omission is a binary fact), `quality` (architecture trade-offs, one rebuttal round on genuine conflict), and `conform` — the one mode for work you *didn't* write: it checks a delegated worker's diff against the brief it was given (including whether its completion report is true), reports deviations as fact, and deliberately stops short of judging whether to keep the work. |
| [`dispatch`](./dispatch) | Delegates coding tasks to external agent CLIs as writable **worker subagents** — Codex (ChatGPT sub), Gemini (Antigravity `agy`), and DeepSeek (opencode, free). Splits work into self-contained briefs, runs workers in parallel (optionally in isolated git worktrees), then this session reviews and merges. Each worker runs through its **own subscription/sign-in CLI**, no API keys. Includes `dispatch.py`, plus an opt-in **auto-dispatch** layer: a `UserPromptSubmit` hook (`dispatch-nudge.sh`) that, on bulk/parallel/mechanical prompts, forces a one-line DISPATCH-or-SELF verdict before any code is written, governed by `dispatch-policy.md`. |

## Tools

Not a skill, but lives here too:

| Tool | Purpose |
|---|---|
| [`autocontinue`](./autocontinue) | Auto-resumes a Claude Code session after a usage-limit reset. A `StopFailure` hook queues the interrupted session; a launchd agent (every 5 min) resumes it headlessly once the limit resets, reusing the original permission mode and stopping after a configurable chain limit. Install once via `autocontinue/install.sh` — applies to all sessions. macOS only. |
| [`action`](./action) | The proactive sibling of `autocontinue` (and a skill): schedules a Claude Code task to start at an **absolute wall-clock time** and run headless/unattended, so heavy or batch work lands in an off-peak window and spreads load across the rolling usage limits. `action add --at 3am --cwd ~/proj "<prompt>"` queues a job; a launchd agent (every 5 min) launches `claude -p` once its start time passes. Composes with `autocontinue` (a scheduled run that hits a limit is resumed after reset). Install via `action/install.sh`. macOS only. |

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

> **Note**: `handoff`'s SessionStart hook must be registered separately in `~/.claude/settings.json` and references `~/.claude/skills/handoff/check-handoff.sh`. Handoffs written before per-project scoping still sit unscoped in `$TMPDIR`; the hook counts them when the current project has none, so you can file or discard them.
>
> **Note**: `FableAdvisor` installs under its skill name: `cp -R FableAdvisor ~/.claude/skills/fable-advisor`. It expects `grill-me` to be installed too (Phase 1 invokes it).
>
> **Note**: `write-register` installs via `write-register/install.sh` — it copies the skill, installs the output style to `~/.claude/output-styles/write-register.md`, sets `outputStyle` in `~/.claude/settings.json`, and retires `caveman`/`stop-slop` into `~/.claude/write-register-superseded/`. `uninstall.sh` reverts all of it (and only clears `outputStyle` if it is still `write-register`, so a style you picked later survives). The style is the part that matters: without it the skill only fires when you name it, and automatic register selection never happens.
>
> **Note**: `dispatch`'s auto-dispatch layer (nudge hook + policy) installs via `dispatch/install.sh` — it copies the skill/hook/policy, registers the `UserPromptSubmit` hook in `~/.claude/settings.json`, and `@`-includes the policy from `~/.claude/CLAUDE.md`. Safe to re-run to update.
