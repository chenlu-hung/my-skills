---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree — then turn the agreed plan into a task list and dispatch the parallelizable work to model subagents. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
---

## Phase 1 — Grill

Interview me to pin down this plan, walking the design tree and settling upstream decisions before the ones that depend on them, so we never reopen a closed branch.

**Only ask load-bearing questions.** Before asking anything, run it through this three-part test — a question earns a slot only if **all three** hold:
1. The answer changes what gets built downstream (if every answer leads to the same build, drop it).
2. The codebase can't answer it (if it can, go read the codebase instead of asking).
3. There's no obvious default (if there is, state the default in one line and move on — don't ask).

Relentless means thorough on what matters, not exhaustive on everything. If a whole round of candidate questions fails the test, the grilling is done — go write the decision log.

**Never repeat yourself.** Maintain a running decision log, one line per settled decision: `#N <decision>: <answer> (implies: <what this forecloses or forces>)`. Before asking, scan the log: skip anything that restates a settled branch, is already implied by a prior choice, or is a second phrasing of the same decision. When in doubt, infer from what I've said rather than re-asking.

**Batch related decisions.** When several discrete decisions sit at the same level and don't depend on each other, ask them together in one `AskUserQuestion` call (up to 4) rather than drip-feeding near-duplicates. Reserve one-at-a-time for genuine dependencies, where my answer steers the next question.

For each question give your recommended answer and a one-line why. For discrete options use `AskUserQuestion` with the recommendation first, labeled "(Recommended)"; use plain text for open-ended "why" / "what-if" probing.

Be terse — no preamble, no recapping what I just said, no restating the question before asking it.

Stop when every load-bearing branch is resolved (or I call it). Then give a short decision log: resolved decisions, plus anything deferred or still open.

## Phase 2 — Plan & task list

Turn the decision log into a plan: an **ordered task list** where each task names its owner, its dependencies (what must land first), and its acceptance criteria ("done" = passes which test / produces what). Order so nothing depends on a task that runs after it.

Tag each task as **worker-eligible** or **stays-with-me**, by the same rule as `dispatch-policy.md`:

- **Worker-eligible** — self-contained, mechanical or parallelizable, and fully specifiable from the decision log without this conversation's context: boilerplate, batch refactors across files, test scaffolding, docstrings, codegen.
- **Stays-with-me** — needs design judgment, dense back-and-forth, the live context of this session, or touches security-sensitive code. Anything too small to be worth the dispatch overhead also stays.

Show the plan and the owner split. This is the checkpoint — the worker tasks are about to write to the repo, so I see the split before anything fans out.

## Phase 3 — Dispatch

If there are worker-eligible tasks, hand them to the **`dispatch` skill** — it owns the mechanics (self-contained briefs, git-worktree isolation for parallel tasks, running the workers, and **verify-before-merge**). Your job here is to set it up well:

- **Bake the decisions into each brief.** Workers are stateless one-shot CLIs that never see this conversation or the decision log — so every choice the task depends on (the resolved decisions, file paths, conventions, acceptance criteria) goes into its brief verbatim. This is the whole reason grilling-then-dispatching works: the grill produced the context the brief needs.
- **Pick a worker per task** — `codex` for nontrivial code, `gemini` (`agy`) for large-context reading/refactor, `opencode` for cheap mechanical chores. State who got what and why, in one line.
- **Parallel tasks on the same repo → worktrees** so they can't clobber each other or my tree.

Then, per `dispatch-policy.md`: declare the split in one line and send — don't wait for a nod — **unless I stop you**. Never merge blind: read each diff against its brief and run the tests before merging; a worker that returns `ok: false` means you do that task yourself or reassign it — surface it, don't hide it. Keep the tasks I own for yourself rather than forcing them through a worker.

Close with a per-task summary: owner, what changed, verification result, and anything you fixed or rejected.
