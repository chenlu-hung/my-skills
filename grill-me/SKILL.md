---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree — then write the agreed plan as a task list and, once approved, work through it in dependency order, dispatching the parallelizable tasks to model subagents. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
---

## Phase 1 — Grill

Interview me to pin down this plan, walking the design tree and settling upstream decisions before the ones that depend on them, so we never reopen a closed branch.

**Only ask load-bearing questions.** Before asking anything, run it through this three-part test — a question earns a slot only if **all three** hold:
1. The answer changes what gets built downstream (if every answer leads to the same build, drop it).
2. The codebase can't answer it (if it can, go read the codebase instead of asking).
3. There's no obvious default (if there is, state the default in one line and move on — don't ask).

Relentless means thorough on what matters, not exhaustive on everything. If a whole round of candidate questions fails the test, the grilling is done — go write the decision log.

**Don't talk through what only a prototype can settle.** Some questions I can't answer until I have something to react to — "how should this interaction feel?", "one long form or three pages?". Don't rephrase and re-ask: log it under `## Open` as `#N <decision>: PROTOTYPE — <what to build to decide it>` and move on. Branches that depend on it stay open.

**Call out contradictions with the code.** When I say how something works, check the code. If they disagree, quote it and ask which is right — "`orders.py:42` cancels the whole order, but you just said partial cancellation works. Which is right?" Never silently side with either.

**Pin down fuzzy terms.** When I use a vague or overloaded word, propose one canonical term and ask — "'account': the Customer or the User?". Record settled terms under the log's `## Terms` as `<Term>: <what it is, one sentence> (avoid: <rejected synonyms>)`. Only project-specific concepts, not general programming ones like timeout or retry; and a definition, never a spec — how it's built goes in a decision line. This pays off in Phase 3: a word that reads two ways in a brief gets built the wrong way.

**Never repeat yourself.** Maintain a running decision log, one line per settled decision: `#N <decision>: <answer> (implies: <what this forecloses or forces>)`. Before asking, scan the log: skip anything that restates a settled branch, is already implied by a prior choice, or is a second phrasing of the same decision. When in doubt, infer from what I've said rather than re-asking.

**Keep the log on disk.** When the first decision settles, write the log to `docs/plans/<YYYY-MM-DD>-<slug>.md` in the repo (no repo — a non-code subject — keep it in the conversation), and update it after every round, not at the end. A compacted context loses exact answers first (ordering guarantees, "must not" requirements, numeric defaults); the file is what Phases 2–3 read from. It is the plan's only doc — later phases, and `fable-advisor`, add their sections to it rather than starting another. Layout:

```markdown
# <plan>

## Decisions
#1 <decision>: <answer> (implies: <what this forecloses or forces>)

## Terms
<Term>: <what it is> (avoid: <rejected synonyms>)

## Open
#N <decision>: deferred | PROTOTYPE — <what to build to decide it>
```

**Batch related decisions.** When several discrete decisions sit at the same level and don't depend on each other, ask them together in one `AskUserQuestion` call (up to 4) rather than drip-feeding near-duplicates. Reserve one-at-a-time for genuine dependencies, where my answer steers the next question.

For each question give your recommended answer and a one-line why. For discrete options use `AskUserQuestion` with the recommendation first, labeled "(Recommended)"; use plain text for open-ended "why" / "what-if" probing.

Be terse — no preamble, no recapping what I just said, no restating the question before asking it.

Stop when every load-bearing branch is resolved (or I call it). Then give a short decision log: resolved decisions, plus anything deferred or still open.

**ADRs — only when all three gates hold.** At the close, check each resolved decision:
1. Hard to reverse — changing course later costs real work.
2. Surprising without context — a future reader of the code would ask "why on earth this way?"
3. A real trade-off — there were genuine alternatives and one was picked for specific reasons.

Any gate fails → it stays a log line; most sessions produce zero ADRs. If any pass all three, ask which to write in one multi-select `AskUserQuestion`. Write each accepted one to `docs/adr/NNNN-<slug>.md` — next number after the highest existing, directory created only when the first ADR is written; if the repo already keeps ADRs elsewhere or in another format, follow its convention. Body: a title plus 1–3 sentences (context, decision, why); add `Considered options` only when the rejected alternative would otherwise get proposed again.

## Phase 2 — Plan & task list

Turn the decision log into a plan — re-read the log file, don't work from memory — and write it into the same file under `## Tasks`, one line per task, ordered so nothing is blocked by a task below it:

```markdown
## Tasks
- [ ] T1 <task> — files: <paths> — owner: worker | session — blocked by: <T#, … | none> — done when: <which test passes / what it produces>
```

Each `PROTOTYPE` item becomes a throwaway prototype task, ordered first. Tasks under its branch are `blocked by` it; once I've reacted to the prototype, settle the branch in one log line and fill in those tasks.

Set each task's owner by the same rule as `dispatch-policy.md`:

- **worker** — self-contained, mechanical or parallelizable, and fully specifiable from the decision log without this conversation's context: boilerplate, batch refactors across files, test scaffolding, docstrings, codegen.
- **session** — this session does it: needs design judgment, dense back-and-forth, the live context of this session, or touches security-sensitive code. Anything too small to be worth the dispatch overhead also stays.

**Stop here until I approve.** Show the file path and the task list, then ask once: is the granularity right, is each task blocked only by what genuinely gates it, should anything merge or split? Apply my changes to the file and ask again until I approve. No code changes before that — this is the one checkpoint, and it covers every dispatch in Phase 3.

## Phase 3 — Execute

Work the plan by its **frontier**: the unchecked tasks whose blockers are all checked. Repeat until every task is checked:

1. **Start the frontier.** Its worker tasks go to the `dispatch` skill together; its session tasks you do yourself, one at a time, while the workers run (if its `files:` overlap a running worker's, wait for that merge first).
2. **Verify each finished task** against its `done when`: run the test yourself, and for worker output read the diff against its brief before merging. Never merge blind.
3. **Check it off in the file right after it passes**, before starting anything it unblocks: `- [x] T3 … — verified: <real result line>`. The file, not your memory, says what's done — it's what a compacted or handed-off session resumes from.
4. **Off-plan → stop.** A worker returning `ok: false`: do it yourself or reassign, and note it on the task line. A task that can't pass as specified, or a discovery that contradicts a logged decision: stop and settle it with me, update the log line first, then continue. Never reshape the plan silently.

For the worker tasks, the **`dispatch` skill** owns the mechanics (self-contained briefs, git-worktree isolation for parallel tasks, running the workers, and **verify-before-merge**). Your job is to set it up well:

- **Bake the decisions into each brief.** Workers are stateless one-shot CLIs that never see this conversation or the decision log — so every choice the task depends on (the resolved decisions, file paths, conventions, acceptance criteria) goes into its brief verbatim, copied from the log file. Include every `Terms` entry the task touches, so the worker names things the way we agreed. This is the whole reason grilling-then-dispatching works: the grill produced the context the brief needs.
- **Pick a worker per task** — `codex` for nontrivial code, `gemini` (`agy`) for large-context reading/refactor, `opencode` for cheap mechanical chores.
- **Parallel tasks on the same repo → worktrees** so they can't clobber each other or my tree.

Declare each dispatch in one line (who got what, and why) and send — my plan approval already covers it, per `dispatch-policy.md`.

Close with a per-task summary: owner, what changed, verification result, and anything you fixed or rejected.
