---
name: handoff
description: Manages context transfer between AI coding sessions. Creates a compact handoff document so a fresh agent can continue work. Use when user says "handoff", "hand off", "resume", "continue later", "pick up where we left off", "transfer context", or when wrapping up a significant session.
argument-hint: "What will the next session be used for?"
---

# Handoff

Transfer context to a fresh session via a compact handoff file. Invoke this skill when the user wants to pause, resume, or pass work to another session.

## Modes

| Invocation | What it does |
|---|---|
| `/handoff` | Full handoff — context, decisions, dead ends, next steps |
| `/handoff quick` | Minimal — one-line goal, suggested skills, 3-5 next steps |
| `/handoff resume` | Continue from an existing handoff file |

If arguments describe the next session's focus, tailor "Next Steps" and "Suggested skills" to it.

## Creating a Handoff

Save to **`$TMPDIR/claude-handoff-<YYYY-MM-DD-HHMM>.md`** (on Windows use `%TEMP%`). The SessionStart hook auto-detects files matching `claude-handoff-*.md`, so keep that prefix.

Run the **Project map check** (below) first. If the map is missing or stale, **don't build it now** — you're wrapping up and it costs tokens. Instead record it as a Next Step (e.g. `[P1] Run /project-map update (map stale)`) so the resuming session does it with fresh budget.

### Full document

```markdown
<!-- HIGHLY SENSITIVE. Do not share this file. -->
# Handoff — [One-line Goal]

> **Suggested skills**: [skill-1], [skill-2], ...

## What We're Building
[1-3 bullets max. Reference artifacts by path/URL — do not duplicate. If `.projectmap/` exists, link `.projectmap/ARCHITECTURE.md` instead of re-describing structure.]

## Progress
- [x] Done item
- [ ] In-progress item (blocked by X)

## What Worked / Avoid
- ✓ Approach A worked
- ✗ Approach B failed — reason (don't retry)

## Key Decisions
- Chose X over Y because ...

## References
- Codebase map (if present): `.projectmap/ARCHITECTURE.md`, `.projectmap/modules/<name>.md`
- Docs: `docs/prd.md`, `docs/adr/001-choice.md`
- Commits: `abc1234`, `def5678`

## Next Steps
1. [P0] Critical action item
2. [P1] Important follow-up
3. [P2] Nice to have
```

**Keep it lean** — the next session reads this back, so every line costs tokens twice (to write, then to re-read):
- Bullets only, no prose. Hard cap ~5 bullets per section; delete any section that would be empty.
- Pointers over content: link commits, diffs, `.projectmap/`, PRDs — never paste their contents.

**Quick mode**: keep only the goal, suggested skills, and 3-5 next steps.

## Resume Flow

Triggered when the user confirms a resume — either after the SessionStart hook reports a handoff, or via `/handoff resume`:

1. Read the handoff file.
2. Load any skills listed under "Suggested skills".
3. Summarize state (goal + progress) for the user.
4. Run the **Project map check** (below). If the map is missing or stale, this is the moment to build/update — offer it, and on confirmation follow the project-map skill's workflow before continuing. Then use the map to regain context: read `ARCHITECTURE.md`, grep `.projectmap/tags`, open only the source files the next step needs. Don't re-scan the repo.
5. Start from the highest-priority "Next Steps" item.
6. Append updates as work progresses (check off items, add new ones) — don't rewrite the whole file.

## Project map check

A committed `.projectmap/` lets the next session skip re-scanning the repo (see the project-map skill). Detect its state with this **read-only, no-LLM-token, no-write** command — run it both when creating and when resuming:

```sh
python3 ~/.claude/skills/project-map/build-map.py status .
```

Interpret the output:

| Output contains | State | Action |
|---|---|---|
| `no source files found` | not a code repo | skip — project map is irrelevant |
| `no manifest yet` | no map | **building costs tokens + needs `universal-ctags`** — never silent |
| `Run /project-map update to refresh` | stale | incremental update (cheaper) |
| `Map is up to date` | current | nothing to do |

Then act by lifecycle moment — **detection is automatic; building/updating is not silent**:

- **Resuming** (fresh budget, pays off this session): if stale/missing, offer to run `/project-map update` or `/project-map build`. On confirmation, follow the project-map skill's workflow, then continue the resume.
- **Creating** (wrapping up — don't spend build tokens now): record the state as a Next Step instead, e.g. `[P1] Run /project-map update (map stale)` or `[P2] /project-map build — no map yet; would cut next session's exploration`.

## Rules

- **Redact** all secrets (API keys, passwords, tokens) and PII before writing.
- Save only to the OS temp dir — **never** the project workspace.
- First line of every handoff: `<!-- HIGHLY SENSITIVE. Do not share this file. -->`
- Reference existing artifacts (PRDs, ADRs, issues, commits, diffs, `.projectmap/`) by path/URL — never duplicate their content.

## When to Suggest

Proactively offer a handoff when the user says "I need to go" / "let's wrap up", at a milestone, or when the conversation has grown long and context-heavy:

> "Want me to create a handoff so another session can pick this up?"
