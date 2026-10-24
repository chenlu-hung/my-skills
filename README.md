# my-skills

Personal collection of [Claude Code](https://claude.com/claude-code) Agent Skills.

**Design principle — model-robust by construction.** These skills are written to degrade gracefully on smaller driving models (e.g. Opus instead of Fable): taste-based judgment is replaced with explicit decision tests and thresholds, multi-step workflows carry non-skippable ordered checklists and literal output templates, and anything a script can do deterministically (shuffling/anonymizing council answers, indexing, change detection) lives in a script instead of a prompt.

| Skill | Purpose |
|---|---|
| [`handoff`](./handoff) | Context transfer between AI coding sessions — creates a compact handoff doc so a fresh agent can resume. Handoffs are **scoped per project** under `~/.claude/handoff/<project>-<hash>/`, so several projects in flight never cross-contaminate; a SessionStart hook (`check-handoff.sh`) offers a resume only for the project you opened, only into a fresh context (startup or `/clear`), and only once per handoff. `handoff-path.sh --link` optionally surfaces them in-tree as `<project>/.claude/handoff` — a symlink, so `git clean -xdf` costs you the link and not the content, and the hook restores it. Saving goes through `handoff-path.sh --commit <draft>`, which **scans for secrets and refuses to file the handoff on a hit** — a gate rather than a checklist item, because handoffs get written exactly when context is running out and a manual step is easiest to skip. It reports masked hits and never rewrites the draft: an altered document is harder to notice than a refusal. Filing appends a **git snapshot** (branch, HEAD, uncommitted files), the facts a hurried writer leaves out, and `--resume` reports what moved since. Integrates with `project-map` (below) to keep docs lean and cut resume-time exploration. |
| [`grill-me`](./grill-me) | Stress-tests a plan or design by interviewing you relentlessly until the decision tree is resolved. |
| [`FableAdvisor`](./FableAdvisor) | Codifies the Anthropic-recommended architect/implementer split: Fable 5 (the session) runs `grill-me`'s interview, designs the architecture into a self-contained doc, then hands off to an **implementer subagent** (default Sonnet 5; overridable to opus/haiku, e.g. `/fable-advisor opus`) that implements against the doc and consults Fable (via `CONSULT` → SendMessage ruling) when blocked on design; Fable reviews the diff against the doc before closing. Installs as `fable-advisor`. |
| [`write-register`](./write-register) | Picks the writing register per output segment, automatically. One test decides it — *will this text still exist after the conversation ends?* — routing to **CHAT** (dense but not telegraphic: it deletes whole moves like restatement and self-summary, and keeps the grammar), **DOC** (natural, self-contained prose for anything that gets saved, with 中文 and English AI-tell lists), or **CODE** (byte-exact, prose rules off). Supersedes `caveman` and `stop-slop`, which contradicted each other — one wanted fragments, the other complete sentences, and neither knew which text it was looking at. Ships as a Claude Code **output style** (`keep-coding-instructions: true`, so the built-in engineering instructions stay), because a classifier that isn't in the system prompt can't classify; the tell lists and `check`/`fix` modes stay on demand in the skill. |
| [`project-map`](./project-map) | Builds a committed, on-demand `.projectmap/` index (ctags symbols + an import-based module dependency graph + short module summaries) so agents remember the codebase and grep the map instead of re-scanning the repo. `affected` lists the modules and tests a change reaches; generated and oversized files are kept out of the symbol lists. The script keeps a marker-fenced pointer in `CLAUDE.md` and in `AGENTS.md` / `GEMINI.md` when present, so Codex, opencode and Gemini workers use the map too; a SessionStart hook (`check-map.sh`) names the modules whose docs went stale. Includes `build-map.py`; requires `universal-ctags`. |
| [`llm-council`](./llm-council) | Convenes a multi-model council — ChatGPT (the desktop app, via `chatgpt-ask`), Gemini (Antigravity `agy`), and Claude (`claude -p`), with Codex (`codex exec`) standing in wherever the desktop app can't reach — to answer a question, cross-review each other anonymously, then this session chairs the synthesis. Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council); every member runs through its **own subscription/sign-in CLI**, no API keys. Includes `council.py` (parallel dispatch) and `test_council.py` (regression tests for the read-only/borrowed-directory guarantees). |
| [`review-me`](./review-me) | Sends *your own* plan or diff to independent external models (Codex, Gemini, optionally a fresh Claude) to find what you **missed** — the characteristic failure of one strong model working alone. Reviewers run **inside the real repo, read-only** (via `council.py --workdir`), because hand-picked context would just reproduce your blind spot. Four modes: `plan` (pre-code omissions), `gap` (post-code omissions, no debate — an omission is a binary fact), `quality` (architecture trade-offs, one rebuttal round on genuine conflict), and `conform` — the one mode for work you *didn't* write: it checks a delegated worker's diff against the brief it was given (including whether its completion report is true), reports deviations as fact, and deliberately stops short of judging whether to keep the work. |
| [`chatgpt-bridge`](./chatgpt-bridge) | Asks the ChatGPT desktop app one question from the command line — `chatgpt-ask --prompt "…"` — and prints the answer on stdout. Answers come out of the ChatGPT **conversation** allowance rather than the Codex quota `codex exec` spends, and no flag can change that: the pool follows the endpoint, and a subscription's only programmatic endpoint is the Codex one, so driving the app's own UI is what reaches the other pool. Questions go into a **temporary chat**, never the account's history, and the (unauthenticated) debugging port is opened per call instead of left on. For research collaboration it can also, explicitly, work inside a **ChatGPT project** or a **saved thread** (`--project`, `--chat`), read a whole thread back as markdown with math, tables and citations intact (`--read`), and list a project's chats and instructions (`--list-project`). `install.sh` links it in as a skill for **Claude Code, Codex, and opencode** at once, all three reading one `SKILL.md`; the script recognises hosts that must not relaunch the app — a session running inside ChatGPT.app, or a Codex sandbox — and refuses rather than closing the window it lives in. `llm-council`'s `chatgpt` member runs through it. |
| [`dispatch`](./dispatch) | Delegates coding tasks to external agent CLIs as writable **worker subagents** — Codex (ChatGPT sub), Gemini (Antigravity `agy`), and DeepSeek (opencode, free). Splits work into self-contained briefs, runs workers in parallel (optionally in isolated git worktrees), then this session reviews and merges. Each worker runs through its **own subscription/sign-in CLI**, no API keys. Includes `dispatch.py`, plus an opt-in **auto-dispatch** layer: a `UserPromptSubmit` hook (`dispatch-nudge.sh`) that, on bulk/parallel/mechanical prompts, forces a one-line DISPATCH-or-SELF verdict before any code is written, governed by `dispatch-policy.md`. |

## How `handoff` + `project-map` compose

They attack the two halves of resume cost. `project-map` carries the *codebase structure* (where things are); `handoff` carries the *session state* (decisions, dead ends, next steps). `handoff-path.sh --resume` runs `project-map`'s read-only `status` check and reports the map as `current`, `stale` or `missing`, so the resuming session (fresh budget, and the one that benefits) can offer to `update`/`build` it and then read the map instead of re-scanning the repo. Creating a handoff doesn't check: wrap-up is when context is scarcest, and the resume checks again anyway.

A current map also lets handoff docs link `.projectmap/ARCHITECTURE.md` instead of re-describing structure, so the doc the next session reads back stays small.

## Install

Copy any skill folder into `~/.claude/skills/`:

```sh
cp -R handoff ~/.claude/skills/
```

> **Note**: `handoff`'s SessionStart hook must be registered separately in `~/.claude/settings.json`:
>
> ```json
> "hooks": { "SessionStart": [ { "matcher": "startup|clear",
>   "hooks": [ { "type": "command", "command": "bash ~/.claude/skills/handoff/check-handoff.sh" } ] } ] }
> ```
>
> The script also ignores `compact` and `resume` events itself, so an offer never lands mid-session even without the matcher.
>
> To use it from another harness, **link rather than copy** — `ln -s ~/.claude/skills/handoff ~/.agents/skills/handoff` — so both installs share one copy of the scripts and one store, and a handoff written on either side resumes on the other. Only the trigger differs: Claude Code's hook offers a resume unprompted, elsewhere the user asks and the skill runs `--resume`. The store is keyed on the enclosing repo root (the physical path, `HANDOFF_PROJECT_DIR` overrides), whichever directory the session or the hook started from, so the hook always looks where the agent filed. Moving a project changes its key: move its store directory along with it.

> **Note**: `project-map` has its own SessionStart hook, `check-map.sh`, which tells a fresh session which modules changed since the map was last built, so the agent reads their source instead of stale docs. It never updates the map itself and is silent when the map is current or the project was never mapped. Register it beside handoff's, in the same `startup|clear` entry:
>
> ```json
> { "type": "command", "command": "bash ~/.claude/skills/project-map/check-map.sh", "timeout": 15 }
> ```
>
> Install by symlink (`ln -s <repo>/project-map ~/.claude/skills/project-map`) so the hook and `build-map.py` stay in step with the repo; `~/.agents/skills/project-map` can then link to that.
>
> **Note**: `FableAdvisor` installs under its skill name: `cp -R FableAdvisor ~/.claude/skills/fable-advisor`. It expects `grill-me` to be installed too (Phase 1 invokes it).
>
> **Note**: `write-register` installs via `write-register/install.sh` — it copies the skill, installs the output style to `~/.claude/output-styles/write-register.md`, sets `outputStyle` in `~/.claude/settings.json`, and retires `caveman`/`stop-slop` into `~/.claude/write-register-superseded/`. `uninstall.sh` reverts all of it (and only clears `outputStyle` if it is still `write-register`, so a style you picked later survives). The style is the part that matters: without it the skill only fires when you name it, and automatic register selection never happens.
>
> **Note**: `chatgpt-bridge` installs itself into all three agent CLIs via `chatgpt-bridge/install.sh`, by symlink rather than by copy, and also puts `chatgpt-ask` on `PATH`. It needs a signed-in ChatGPT.app, and either conda (the installer makes a `chatgpt-bridge` env) or `uv`; `chatgpt-ask --doctor` reports whether the host you are calling from can reach it.
>
> **Note**: `dispatch`'s auto-dispatch layer (nudge hook + policy) installs via `dispatch/install.sh` — it copies the skill/hook/policy, registers the `UserPromptSubmit` hook in `~/.claude/settings.json`, and `@`-includes the policy from `~/.claude/CLAUDE.md`. Safe to re-run to update.
