---
name: dispatch
description: Delegates coding tasks to external agent CLIs as if they were subagents — Codex (ChatGPT sub), Gemini (Antigravity `agy`), and DeepSeek (opencode, free) — each through its own subscription/sign-in CLI, not an API key. Workers get write access and can run in isolated git worktrees for parallel work; this Claude Code session orchestrates, reviews, and merges. Use when the user says "dispatch to", "delegate to codex/gemini/opencode", "讓 codex 做", "派工", "have another model do it in parallel", "farm this out", or invokes "/dispatch".
argument-hint: "<worker>: \"<task>\" | \"<task to split across workers>\""
---

# Dispatch

Use external coding-agent CLIs as **worker subagents**: split the work, write one
self-contained brief per task, run the workers in parallel, then review and merge
what they produced. Sibling of `llm-council` (same CLIs, same subscription auth),
but where the council *answers a question read-only*, dispatch *does work with
write access* — so isolation and review are the core of the skill.

## Workers

| Worker | Reached via | Write mode | Auth (subscription / sign-in, not API key) |
|---|---|---|---|
| **codex** | `codex exec` | `-s workspace-write` sandbox (no network) | ChatGPT subscription (`auth_mode: chatgpt`) |
| **gemini** | Antigravity `agy -p` | `--dangerously-skip-permissions` | Google Antigravity sign-in |
| **opencode** | `opencode run` | `--dangerously-skip-permissions` | opencode sign-in — default DeepSeek V4 Flash (free) |

Claude-side work does **not** go through this skill — use the native Agent tool for
that. Dispatch is specifically for putting *other* models to work. The CLIs are
stateless one-shot calls: a worker sees only its brief and its directory, never this
conversation.

## How to invoke

| Invocation | Meaning |
|---|---|
| `/dispatch codex: "fix the failing tests in pkg/auth"` | Single delegation to a named worker |
| `/dispatch "add docstrings to every module in src/"` | You choose the worker(s) and the split |
| `/dispatch parallel: "<big task>"` | Force a multi-worker fan-out with worktrees |

Picking a worker when the user doesn't: **codex** for nontrivial code changes
(strongest worker, sandboxed), **gemini** for large-context reading/refactoring,
**opencode** for cheap mechanical chores (it's free). Say who you picked and why,
briefly.

## Workflow

### 1. Split and brief

Decompose the request into independent tasks. For each, write a **self-contained
brief** to a temp file: the goal, the relevant file paths, project conventions worth
obeying, what "done" looks like (e.g. "`npm test` passes"), and any constraint like
"do not touch X". End every brief with: *"When finished, report exactly what you
changed, file by file."* — the report comes back as `report` in the JSON.

The worker has no access to this conversation, CLAUDE.md, or your context. Anything
it needs must be in the brief.

### 2. Choose isolation

- **One task, or tasks in disjoint directories** → run in place (`"dir"`).
- **Parallel tasks touching the same repo** → set `"worktree": true` per task.
  Each gets a fresh worktree at `<repo>.dispatch/<id>` (sibling of the repo) on
  branch `dispatch/<id>`, so workers can't clobber each other or your tree.
- Uncommitted changes in the main tree won't be visible in a worktree — commit or
  mention relevant in-flight state in the brief.

### 3. Dispatch

Single task:
```sh
python3 ~/.claude/skills/dispatch/dispatch.py --worker codex --prompt-file brief.txt --dir <repo>
```
Fan-out — write `tasks.json` and run once; all tasks execute in parallel:
```json
[
  {"id": "auth-fix", "worker": "codex",    "task_file": "brief-auth.txt", "dir": ".", "worktree": true},
  {"id": "docs",     "worker": "opencode", "task_file": "brief-docs.txt", "dir": ".", "worktree": true}
]
```
```sh
python3 ~/.claude/skills/dispatch/dispatch.py --tasks tasks.json
```
Default timeout is 900s per task; raise `--timeout` (or per-task `"timeout"`) for
big jobs. Tell the user up front that workers run for minutes, not seconds.

### 4. Verify — never merge blind

Workers run with auto-approved permissions; their self-report is not evidence.
For each task, the JSON includes `git.changed_files` and `git.shortstat` — what
actually changed. Then:

1. Read the diff (`git -C <dir> diff`) and check it matches the brief — no scope
   creep, no deleted tests, no stray files.
2. Run the project's tests/build against the worker's tree.
3. If a worker failed or did poor work: fix it yourself if small, or re-dispatch
   with a sharpened brief. A worker with `ok: false` (CLI missing, timeout) just
   means you do that task yourself or reassign it — surface it, don't hide it.

### 5. Merge and clean up

For worktree tasks, after verifying: commit in the worktree, merge or cherry-pick
`dispatch/<id>` into the user's branch (or `git diff | git apply` for small
changes), then remove the worktree and branch:
```sh
git -C <repo> worktree remove <wt-dir> --force && git -C <repo> branch -D dispatch/<id>
```
Close with a summary: per task — worker, what changed (files/stats), verification
result, and anything you fixed or rejected.

## Requirements

Each worker is optional — if its CLI is absent or signed out, `dispatch.py` returns
that task as `ok: false` and the rest proceed.

- **`codex`** — signed into a ChatGPT subscription (`codex login`). Its sandbox has
  **no network**: tasks needing `npm install` etc. should go to another worker, or
  pre-install deps yourself first.
- **`agy`** (Antigravity CLI) — signed in for Gemini models.
- **`opencode`** — signed in (`opencode auth`); default DeepSeek model is free.
- **`python3`** (stdlib only) and **`git`** (for worktrees / change summaries).

## Rules

- **Self-contained briefs.** Stateless CLIs, fresh worktrees — every fact the worker
  needs (paths, conventions, acceptance criteria) goes in the brief.
- **You own the result.** Review every diff and run the tests before merging; the
  user holds *you* responsible for worker output, not the worker.
- **Don't dispatch judgment calls.** Tasks needing the user's intent, this
  conversation's context, or architectural taste stay with you; dispatch
  well-specified, verifiable units.
- **Contain the blast radius.** `gemini`/`opencode` run unsandboxed with
  auto-approve — only point them at the task repo, never at `~` or anywhere with
  secrets, and prefer worktrees so a bad run is one `worktree remove` away from
  gone.
- **Clean up worktrees** — never leave `<repo>.dispatch/` litter behind after
  merging or rejecting.

## When to suggest

- The user has several independent chores that could run in parallel while you work
  on the main thread.
- The user explicitly wants another model to do (not just opine on) something —
  "let codex implement it", "have deepseek write the boilerplate".
- A task is mechanical and large (mass renames, docstrings, test scaffolding) —
  cheap to delegate to opencode and verify, freeing you for the hard parts.
