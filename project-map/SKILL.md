---
name: project-map
description: Builds and maintains a compact, on-demand "project map" so coding agents remember a codebase's structure and spend fewer tokens during maintenance. Combines deterministic ctags indexing with short LLM-written module summaries, committed to the repo. Use when the user says "project map", "index this repo", "remember the structure", "map the codebase", "reduce token usage on exploration", or invokes "/project-map".
argument-hint: "build | update | status"
---

# Project Map

Give a fresh agent a queryable, committed map of the codebase instead of making it
re-grep and re-read files every session. A script does the cheap, deterministic work
(ctags symbol index + module grouping + dependency scan); you add a thin semantic layer
(≈3-sentence module summaries). The map lives in `.projectmap/` and is committed to git.

**Why this saves tokens:** day-to-day, the agent reads small Markdown + greps `tags`
instead of scanning the repo. The LLM summaries cost tokens only when building or when a
module actually changes (incremental). The persistent behaviour change lives in the
project's `CLAUDE.md` pointer (below), not here — so it's active every session at almost
no cost.

## Modes

| Invocation | What it does |
|---|---|
| `/project-map` or `/project-map build` | Full reindex: run the script for all modules, then fill any missing summaries |
| `/project-map update` | Incremental: re-index, refresh only changed/new modules, re-summarize those |
| `/project-map status` | Read-only drift report (no ctags, no writes) — tells you if the map is stale |

Optional: pass a project path as a second arg; defaults to the current directory.
Set `PROJECTMAP_DEPTH` (default 2) to change module grouping granularity.

## Requirements

- **`universal-ctags`** on PATH (macOS: `brew install universal-ctags`; Debian/Ubuntu: `apt install universal-ctags`).
- **`python3`** (stdlib only). If ctags is missing, the script says so — tell the user how to install it.

**Swift note:** stock Universal Ctags ships no Swift parser, so a Swift repo would
otherwise index to zero symbols. The skill bundles a regex parser at
`parsers/swift.ctags` and the script auto-loads it (via `--options=`) only when
ctags lacks native Swift support — no extra install needed. If any other language
yields zero symbols, the script prints a warning to stderr so the gap isn't silent.

## Workflow (build / update)

1. **Run the script** (it does the deterministic layer — zero LLM tokens):
   ```sh
   python3 ~/.claude/skills/project-map/build-map.py <mode> [project-path]
   ```
2. **Read its stdout report.** It lists the modules that need a summary (new, changed, or
   never summarized) and the path to each module doc.
3. **For each listed module:** open `.projectmap/modules/<name>.md`. Use its
   `## Public symbols` and `## Dependencies` lists to decide which files to actually read
   — don't read the whole module blindly. Replace the `## Summary` TODO with **≈3 sentences**:
   what the module does, why it exists, its role in the architecture.
4. **Update that module's one-liner** in the `## Modules` table of `.projectmap/ARCHITECTURE.md`.
5. **On first build**, also fill the remaining TODOs in `ARCHITECTURE.md`: `## Overview`,
   `## Entry points` (verify the auto-detected list), and `## Conventions`.
6. **Add the CLAUDE.md pointer** (below) to the project's `CLAUDE.md` if it isn't there.
7. **Tell the user to commit** `.projectmap/` and `CLAUDE.md`. The whole map is meant to be
   version-controlled and shared with teammates.

For `status`, just run the script and relay the drift report; offer `/project-map update` if stale.

## CLAUDE.md pointer

Add this block to the **project's** `CLAUDE.md` (this is the always-on instruction that
actually changes agent behaviour and saves tokens):

```markdown
## Project map
A `.projectmap/` index exists — use it before broad exploration:
- Read `.projectmap/ARCHITECTURE.md` for the module map, entry points, and conventions.
- To locate a symbol, grep `.projectmap/tags` (ctags format) instead of scanning the repo.
- Open `.projectmap/modules/<name>.md` only for the module you're working in.
Re-run `/project-map update` after substantial changes.
```

## Rules

- The script owns every block between the `projectmap:*` markers, plus `tags` and
  `manifest.json`. **Never hand-edit those** — they're regenerated.
- Write only inside `## Summary` and the prose sections of `ARCHITECTURE.md`. Those edits
  are **preserved** across re-runs (including changed modules — you'll just be asked to review them).
- Keep summaries to ~3 sentences. The goal is token economy, not documentation prose.
- Don't duplicate code into the map; reference symbols by `file:line` (already in the docs).
- Everything in `.projectmap/` is meant to be committed — there are no scratch/intermediate files.

## When to suggest

- The user opens an unfamiliar or large repo, or asks the agent to "remember the structure"
  or "stop wasting tokens exploring."
- After a substantial refactor or merge — offer `/project-map update`.
- At the start of a session in a mapped repo, if `/project-map status` would show heavy drift.
