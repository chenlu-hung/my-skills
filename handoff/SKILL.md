---
name: handoff
description: Saves session state to a per-project handoff file so a fresh agent can continue the work, and resumes from it. Use when the user says "handoff", "hand off", "resume", "continue later", "pick up where we left off", "transfer context", "交班", "交接", "接手", "下次繼續", or when wrapping up a significant session.
argument-hint: "[quick|resume] [what the next session is for]"
---

# Handoff

The reader is a fresh agent that never saw this conversation. Make every line stand alone: name files, `file:line`, commands, shas. Never write "the bug", "that approach", "as discussed".

| Invocation | Does |
|---|---|
| `/handoff [focus]` | Full handoff; `focus` steers `Next` and `Skills` |
| `/handoff quick` | Goal, `Skills` and 3-5 `Next` items only |
| `/handoff resume` | Resume steps below |

## Create

1. Write the draft to a scratch file from the template. Bullets only, ≤5 per section, drop empty sections. Point to commits, diffs, docs and `.projectmap/` instead of pasting them. No secrets or personal data.
2. File it; never pick the destination yourself:
   ```sh
   bash ~/.claude/skills/handoff/handoff-path.sh --commit <draft>
   ```
   It scans for secrets, appends a git snapshot (branch, HEAD, uncommitted files), files the handoff, prints its path and deletes the draft. Exit 3 means a possible secret, nothing written, masked lines listed: remove `secret` lines; for `likely` lines, remove the value if real, else rephrase. Re-run.
3. Tell the user the printed path.

Template:

```markdown
# Handoff — <one-line goal>
Skills: <skills the next session should load>

## Context
- <what and why, 1-3 bullets; link the PRD / issue / `.projectmap/ARCHITECTURE.md` instead of re-describing>

## Progress
- [x] <done> (`<sha>`)
- [ ] <in progress> — stopped at <file:line, exact state>
- Verify: `<test/build command>` → <result now>

## Next
1. [P0] <first concrete action>
2. [P1] …

## Decisions & constraints
- <chose X over Y because …>
- <user requirement: always/never …>

## Worked / avoid
- ✓ <non-obvious thing that worked>
- ✗ <tried X; failed because Y — don't retry>

## Open questions
- <undecided: ask the user, don't decide>
```

## Resume

1. Run `bash ~/.claude/skills/handoff/handoff-path.sh --resume`. It prints the newest handoff's path (no output: none for this project), what changed since it was filed, and the project-map state. It also stops the start-up hook from offering that handoff again.
2. Read the handoff and load the skills on its `Skills:` line.
3. If HEAD or the uncommitted files differ from its Snapshot, tell the user before acting on it.
4. On `project-map: stale` or `missing`, offer `/project-map update` or `build` (costs tokens; ask first). Then orient from `.projectmap/` (ARCHITECTURE.md, grep `tags`), not by scanning the repo.
5. Summarize goal and state for the user in ≤3 lines, then start the top `Next` item.
6. Leave the old handoff as it is. At wrap-up, file a new one; it supersedes.

## More

- `handoff-path.sh` with no argument prints this project's handoff directory. `--latest`: newest path, read-only. `--scan <file>`: check without filing. `--keep <n>`: retention (default 5). `--link` / `--unlink` / `--link-status`: in-tree symlink `<project>/.claude/handoff`, git-excluded; only when the user asks.
- Outside Claude Code there is no start-up hook, so resume only when asked. If the shell starts outside the project, set `HANDOFF_PROJECT_DIR`.
- When the user wraps up or the context grows long, offer: "Want me to create a handoff so another session can pick this up?"
