---
name: project-map
description: Builds and maintains a compact, on-demand "project map" so coding agents remember a codebase's structure and spend fewer tokens during maintenance. Combines deterministic ctags indexing and an import-based module dependency graph with short LLM-written module summaries, committed to the repo. Use when the user says "project map", "index this repo", "remember the structure", "map the codebase", "reduce token usage on exploration", "what does this change affect", or invokes "/project-map".
argument-hint: "build | update | status | affected"
---

# Project Map

Give a fresh agent a queryable, committed map of the codebase instead of making it
re-grep and re-read files every session. A script does the cheap, deterministic work
(ctags symbol index + module grouping + import resolution into a module dependency
graph); you add a thin semantic layer (≈3-sentence module summaries). The map lives in
`.projectmap/` and is committed to git.

**Why this saves tokens:** day-to-day, the agent reads small Markdown + greps `tags`
instead of scanning the repo. The LLM summaries cost tokens only when building or when a
module actually changes (incremental). The persistent behaviour change lives in a pointer
block the script writes into the project's agent files (below), not here — so it's active
every session at almost no cost.

## Modes

| Invocation | What it does |
|---|---|
| `/project-map` or `/project-map build` | Full reindex: run the script for all modules, then fill any missing summaries |
| `/project-map update` | Incremental: re-index, re-summarize only changed/new modules |
| `/project-map status` | Read-only drift report (no ctags, no writes) — tells you if the map is stale |
| `/project-map affected` | Read-only: modules and test files that import, directly or transitively, the uncommitted changes |

Optional: pass a project path as a second arg; defaults to the current directory.
Set `PROJECTMAP_DEPTH` (default 2) to change module grouping granularity.

`affected` options: `--base main` (changes vs a ref instead of HEAD), `--stdin` (read
paths, e.g. `git diff --name-only main... | build-map.py affected --stdin`), `--depth N`
(import hops, default 5), `--tests` (only the test file paths, one per line, for a test
runner). It follows imports only, so dynamic dispatch, reflection and runtime loading
are invisible to it — treat the result as a floor, not a proof.

## Requirements

- **`universal-ctags`** on PATH for build/update (macOS: `brew install universal-ctags`; Debian/Ubuntu: `apt install universal-ctags`).
- **`python3`** (stdlib only). If ctags is missing, the script says so — tell the user how to install it.

**Swift note:** stock Universal Ctags ships no Swift parser, so a Swift repo would
otherwise index to zero symbols. The skill bundles a regex parser at
`parsers/swift.ctags` and the script auto-loads it (via `--options=`) only when
ctags lacks native Swift support — no extra install needed. If any other language
yields zero symbols, the script prints a warning to stderr so the gap isn't silent.

## What gets indexed

- Tracked + untracked-not-ignored files with a known source extension, minus build/vendor dirs.
- **Skipped:** files over 1 MB, and anything matched by `exclude` in the optional,
  user-owned `.projectmap/config.json` — for committed vendored or generated trees that
  `.gitignore` can't drop: `{"exclude": ["static/", "**/vendor/**"]}` (gitignore-style; no `!`).
- **Generated files** (protobuf/gRPC, mocks, `*.generated.*`, minified JS, or a
  `Code generated … DO NOT EDIT` style header) stay in `tags` and the file list, marked
  `(generated)`, but are left out of `Public symbols` so they can't crowd out real code.
- **Imports** are resolved to project files for C/C++/ObjC, Python, JS/TS (relative,
  `@/` alias, workspace packages), Go (`go.mod`), Rust (`crate`/`super`/`mod`, workspace
  crates), Swift (SwiftPM targets), Java/Kotlin and Ruby. C# `using` isn't traced.

## Workflow (build / update)

1. **Run the script** (it does the deterministic layer — zero LLM tokens):
   ```sh
   python3 ~/.claude/skills/project-map/build-map.py <mode> [project-path]
   ```
2. **Read its stdout report.** It lists the modules that need a summary (new, changed, or
   never summarized) and the path to each module doc.
3. **For each listed module:** open `.projectmap/modules/<name>.md`. Use its
   `## Public symbols`, `## Depends on` and `## Used by` lists to decide which files to
   actually read — don't read the whole module blindly. Replace the `## Summary` TODO with
   **≈3 sentences**: what the module does, why it exists, its role in the architecture.
   Match this size and register — no headings, no lists, no restating the symbol table:
   > Parses CLI flags and config files into a single validated `Settings` object.
   > Exists so no other module touches raw argv or the config format. Every entry
   > point calls it first; everything downstream consumes `Settings` only.
4. **Update that module's one-liner** in the `## Modules` table of `.projectmap/ARCHITECTURE.md`.
5. **On first build**, also fill the remaining TODOs in `ARCHITECTURE.md`: `## Overview`,
   `## Entry points` (verify the auto-detected list), and `## Conventions`.
6. **Check the pointer line of the report** (see below). If the user also runs Codex,
   opencode or Gemini in this repo and it has no `AGENTS.md`, offer `ln -s CLAUDE.md AGENTS.md`
   and a re-run, so those agents get the pointer too.
7. **Tell the user to commit** `.projectmap/` and the agent files the report named. The
   whole map is meant to be version-controlled and shared with teammates. When parallel
   workers (e.g. `/dispatch` worktrees) edit the repo, run `update` once after merging
   rather than in each worker, so `tags` and `manifest.json` don't conflict.

For `status`, just run the script and relay the drift report; offer `/project-map update` if stale.
For `affected`, relay the module list and test files; offer to run those tests.

## The pointer block

On build/update the script writes a short, marker-fenced `## Project map` block (text in
`POINTER_BODY` in `build-map.py`) into the project's `CLAUDE.md` (or `.claude/CLAUDE.md`;
created if neither exists) and into `AGENTS.md` / `GEMINI.md` when they exist, writing a
symlinked file once and skipping one that points outside the project. It tells every
agent, subagents included, to read the map before grepping, not to re-derive or delegate
what the map lists, and to check "Used by" / `affected` before changing an interface.
An unfenced pointer pasted by an older version is replaced in place.

## Rules

- The script owns every block between the `projectmap:*` markers (in `.projectmap/` and
  in the agent files), plus `tags` and `manifest.json`. **Never hand-edit those** — they're regenerated.
- Write only inside `## Summary` and the prose sections of `ARCHITECTURE.md`. Those edits
  are **preserved** across re-runs (including changed modules — you'll just be asked to review them).
- `.projectmap/config.json` belongs to the user; the script only reads it.
- Keep summaries to ~3 sentences. The goal is token economy, not documentation prose.
- Don't duplicate code into the map; reference symbols by `file:line` (already in the docs).
- Everything in `.projectmap/` is meant to be committed — there are no scratch/intermediate files.

## When to suggest

- The user opens an unfamiliar or large repo, or asks the agent to "remember the structure"
  or "stop wasting tokens exploring."
- After a substantial refactor or merge — offer `/project-map update`.
- Before changing a module's interface or before a commit — `affected` shows what to retest.
- At the start of a session in a mapped repo, if `/project-map status` would show heavy drift.
- During a **handoff** resume: the handoff skill runs `build-map.py status` to detect a
  missing or stale map and offers to `build`/`update` so the fresh session reads this map
  instead of re-scanning the repo. A current map keeps handoff docs small (they link
  `ARCHITECTURE.md` instead of re-describing structure), so the two skills compound.
