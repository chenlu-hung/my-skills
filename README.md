# my-skills

Personal collection of [Claude Code](https://claude.com/claude-code) Agent Skills.

| Skill | Purpose |
|---|---|
| [`handoff`](./handoff) | Context transfer between AI coding sessions — creates a compact handoff doc so a fresh agent can resume. Includes a SessionStart hook (`check-handoff.sh`) that auto-detects handoff files. |
| [`grill-me`](./grill-me) | Stress-tests a plan or design by interviewing you relentlessly until the decision tree is resolved. |
| [`caveman`](./caveman) | Ultra-compressed communication mode — cuts token usage ~75% while keeping technical accuracy. |

## Install

Copy any skill folder into `~/.claude/skills/`:

```sh
cp -R handoff ~/.claude/skills/
```

> **Note**: `handoff`'s SessionStart hook must be registered separately in `~/.claude/settings.json` and references `~/.claude/skills/handoff/check-handoff.sh`.
