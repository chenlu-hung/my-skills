---
name: review-me
description: Sends your own plan, diff, or design out to independent external models (Codex, Gemini, optionally a fresh Claude) to find what you MISSED — the failure mode of a single strong model working alone. Reviewers read the real repo read-only, so they can catch omissions your prompt never mentioned. Three modes: `plan` (before coding), `gap` (after coding, find omissions), `quality` (architecture trade-offs, with a rebuttal round on genuine disagreement). Use when the user says "review me", "check my plan", "did I miss anything", "have codex review this", "second pair of eyes on this diff", "審一下有沒有漏", "看看我漏了什麼", "找遺漏", or invokes /review-me.
argument-hint: "plan [<plan file>] | gap [<git ref>] | quality [<git ref>]"
---

# Review Me

You (a strong model working alone) reliably miss things: the caller you didn't grep for, the
test you didn't update, the edge case the plan never named. This skill hands your work to
**independent external models** and asks them, specifically, what you left out.

It reuses `llm-council`'s dispatcher (`council.py`) with `--workdir`, so reviewers run
**inside the real repo, read-only**.

## The rule that makes this work

> **Never hand-pick the context for the reviewers. Give them the repo and let them look.**

If you summarize the codebase into the prompt, the reviewers can only see what *you* chose to
show — and what you chose to show is exactly what your blind spot already filtered. The
omission gets copied into the review and the whole exercise cancels out. So: pass a real path
via `--workdir`, name the files and commands to look at, and let them read.

## Modes

| Invocation | Reviews | Output |
|---|---|---|
| `/review-me plan [<file>]` | A plan, before any code is written | Omissions in the plan |
| `/review-me gap [<ref>]` | A diff, after coding | Omissions in the implementation |
| `/review-me quality [<ref>]` | The same diff | Architecture trade-offs, disagreements adjudicated |

`<ref>` defaults to the uncommitted working tree; accepts anything `git diff` takes
(`HEAD~1`, `main...HEAD`, a path). If no plan file is given in `plan` mode, use the plan
under discussion in the current conversation — write it to a temp file first.

**Run `gap` before `quality`.** Fixing omissions changes the code, which invalidates quality
comments written against the old version. Never run both in one call — see *Why the modes are
separate* below.

## Reviewers

Defaults to `codex,gemini`. The user may add `claude` — but know what it buys:

- **codex / gemini (heterogeneous)** — failure modes uncorrelated with yours. This is the
  point of the skill; always include at least one.
- **claude (same model, fresh context)** — highly correlated blind spots, so it is *not*
  insurance against the same class of miss. Its one independent value: your session context is
  polluted by your own reasoning path, and a clean-context Claude catches "you forgot the
  constraint you agreed to 40 turns ago". Add it for long sessions, not for coverage.

Pass `--members` to change the roster. A member whose CLI is missing comes back `ok: false`;
carry on with the rest and say who dropped out.

## Workflow

### Step 0 — Freeze the target (all modes)

Reviewers must see the *same* code you are asking about.

1. `git status --porcelain` — a dirty tree is fine (it is the default target), but do not edit
   files while a review is in flight.
2. **Establish provenance before reviewing anything — do not skip this.** Go through the
   changed paths and split them in two: work *this session* did, and everything else. Anything
   that was already dirty when the session started, or that you cannot account for, is in the
   second group — a different agent (a `/dispatch` worker, a Codex or Antigravity run), another
   session, or the user's own editing.

   For that second group, **stop and ask the user** what to do with it. Do not review it, do
   not commit it, do not stage it, and do not fold it into a commit of your own work.

   Two separate reasons, both real:
   - **A dirty target produces dirty findings.** Reviewers cannot tell your change from
     someone else's, so their omissions get attributed to you and the report loses focus.
   - **Someone else's work is not yours to dispose of.** The code looking coherent is not
     evidence the user wants it kept: a plausible, self-consistent diff from another agent is
     exactly what a rejected attempt looks like. That judgment is the user's and you cannot
     substitute a review for it — no reviewer model can tell you whether the user is satisfied
     with work they commissioned elsewhere.

   Normally the answer is to narrow the review with a pathspec (`git diff -- <your paths>`) so
   the target is only what this session actually did.
3. **Prefer reviewing from a committed tree.** The read-only guarantees are not equal: codex
   has an OS sandbox and claude has harness-level tool denial, but gemini only has `--mode
   plan`, a behavioural mode paired with `--dangerously-skip-permissions`. It held under test,
   but it is a promise rather than a wall. If there is uncommitted work you cannot afford to
   lose, commit (or stash) first, or drop gemini from `--members`.
4. Write the review target to a temp file:
   - `plan` mode → the plan text
   - `gap` / `quality` → `git diff <ref> > <tmp>/target.diff`
5. If the diff is empty, stop and say so — there is nothing to review.
6. **Check the diff size.** Prompts reach codex and gemini as command-line arguments, so the
   whole prompt must fit in `ARG_MAX` (1MB on macOS) — that is roughly 20k diff lines. A diff
   anywhere near that is too big to review usefully anyway: split it by subsystem and run one
   review per part rather than truncating.

### Step 1 — Build the prompt

Every prompt must state: the repo path, what to look at, and the output format. Reviewers are
one-shot and stateless — they keep nothing between calls.

**Include a short repo-layout orientation** — the directory convention, and which files are
plausibly related to the change. This is not the same as hand-picking context: you are telling
them where to *start looking*, not what they are allowed to see, and they remain free to read
anything. Without it they blind-scan the tree and burn the timeout before reaching a finding.

**`plan` mode:**
```
You are reviewing a PLAN before it is implemented. The repo is at <abs path> — read it.
Do not write files.

The plan:
<plan text>

Find what this plan MISSES. Read the actual code to check. Specifically hunt for:
- call sites / dependents the plan does not mention but that this change would break
- edge cases, error paths, and concurrency/ordering issues the plan never names
- tests, docs, config, or migrations that must change alongside and are not listed
- stated assumptions that the code contradicts

For each omission output exactly:
  SEVERITY (blocker|should-fix|minor) | file:line or "plan" | what is missing | how you verified it
Verify by citing a real file/symbol you read. If you cannot verify a concern, mark it
UNVERIFIED. Do not comment on style, naming, or code quality — omissions only.
Do not restate what the plan gets right.
```

**`gap` mode:** same shape, with the diff as the target and this hunt list:
```
- callers/dependents of every changed symbol that were NOT updated
- tests that should have changed and did not; new code paths with no test
- docs, types, config, error handling, or migrations left inconsistent with the change
- a case handled in one branch of the change but forgotten in a parallel branch
```

**`quality` mode:**
```
Assume the change is COMPLETE and correct — do not hunt for missing pieces.
Judge architecture and trade-offs only.

Every comment MUST include a concrete alternative and why it is better, naming which
axis improves (correctness | maintainability | performance). If you cannot name a
specific alternative, do not raise the point. Pure preference ("I'd write this
differently", naming/style) is not a comment. Fewer, sharper comments beat a long list.

Format: file:line | the trade-off | your concrete alternative | which axis it improves
```

### Step 2 — Dispatch (parallel)

```sh
python3 ~/.claude/skills/llm-council/council.py \
    --members codex,gemini --workdir <repo abs path> --timeout 900 \
    --prompt-file <tmp>/review.txt > <tmp>/review.json
```

`--workdir` is what lets reviewers read the repo; it also hardens each member read-only
(codex `-s read-only`, agy `--mode plan`, claude write tools denied) and never deletes the
directory. Check `members.<name>.ok` in the JSON.

**Always pass `--timeout 900`.** `council.py`'s 300s default is sized for one-shot Q&A and is
*not* enough here: a real review means dozens of tool calls chasing call sites, and a review
of a 170-line diff timed out both members at 280s in testing. A timeout returns `ok: false`
with nothing salvageable — you pay the full wall clock and get zero findings.

Because the run is long, **tell the user before dispatching** that it will take several
minutes, and run it in the background so they can keep working.

Timing for a trivial one-file question, as a floor, not a forecast: claude ~5s, codex ~15s,
gemini ~130s. Gemini dominates the wall clock in every mode — consider dropping it when the
user wants a fast pass. It also prefixes answers with narration ("I'm finding the file
now…"); that is plan mode, not a finding. Ignore it.

### Step 3 — Merge (`plan` / `gap`)

**Do not run a rebuttal round in these modes.** An omission is a binary fact: one reviewer
spotting it makes it real, and the others not spotting it is not evidence against — they are
the same kind of miss you made. Voting here would delete true findings.

1. **Verify before reporting.** Each finding names a file/line — go check it. Reviewers
   working from a partial read *will* claim missing callers that exist and tests that are
   already there. Drop what does not survive; keep the finding's own `UNVERIFIED` marks.
2. Deduplicate: same omission from two reviewers = one entry, note the corroboration.
3. Sort by severity, blockers first.
4. Report:
   - **Confirmed omissions** — file:line, what's missing, which reviewer(s), your verification
   - **Dropped** — one line each for claims you checked and found wrong (this keeps the
     reviewers honest and tells the user how noisy the run was)
   - **Dropouts** — any member that errored

Then ask whether to fix them. Do not start fixing unprompted.

### Step 4 — Adjudicate (`quality` only)

Quality comments are judgments, not facts, so here disagreement is informative.

1. **Bucket** the comments: **consensus** (≥2 reviewers agree), **single-voice**, and
   **conflicting** (reviewers recommend *opposite* things about the same code — e.g. "extract
   this abstraction" vs "this abstraction is premature").
2. **Rebuttal round — conflicts only, exactly one round.** For each conflict, send each side's
   comment back to *its own author* with the opposing view quoted anonymously:
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --members <author> \
       --workdir <repo abs path> --timeout 900 --prompt-file <rebuttal.txt>
   ```
   ```
   You reviewed the code at <abs path> and wrote:
   <their comment>

   Another reviewer argued the opposite:
   <opposing comment, verbatim, no attribution>

   Defend your position with specifics from the code, or concede. If you concede, say
   exactly what changes. Do not restate your original comment.
   ```
   Run these in parallel. **Never loop for a second round** — extra rounds on judgment calls
   make models converge on whoever sounds most confident rather than whoever is right.
3. **You adjudicate.** You have repo context the reviewers lack. Rule each conflict
   **defended / conceded / still open**, and say which you would take. "Still open" is a
   legitimate outcome — report the trade-off honestly instead of manufacturing a verdict.
4. Report: consensus items, adjudicated conflicts with verdicts, then single-voice items last.

## Why the modes are separate

Three reasons, all load-bearing — do not "save a call" by merging them:

- **Attention budget.** Quality comments are cheap to write; finding omissions is grunt work
  (chasing callers, diffing against tests). Asked for both at once, a model spends its budget
  on the cheap half and the omissions thin out.
- **Different epistemics.** Omissions are binary and verifiable — you should act on them.
  Quality is a matter of degree, arguable, sometimes rightly rejected. Merged into one list,
  the certainty of the first is diluted by the subjectivity of the second and the user cannot
  tell which findings to trust.
- **Order dependency.** Fixing omissions changes the code, invalidating quality comments
  written against the old version.

## Requirements

- `codex` (ChatGPT sub), `agy` (Antigravity), `claude` — each optional; missing ones drop out.
- `council.py` from the `llm-council` skill, with `--workdir` support.
- `python3` (stdlib only), `git`.

## Rules

- **Reviewers get the repo, not your summary** — hand-picked context reproduces your blind
  spot. Always pass `--workdir` with a real path.
- **Review only what this session did.** Changes of unknown provenance get raised with the
  user, never reviewed, committed, or absorbed into your work. This skill finds omissions; it
  cannot tell you whether the user wants someone else's work kept, and a coherent-looking diff
  is not evidence that they do.
- **Verify every finding against the code before reporting it.** You are the filter; passing
  through unchecked claims makes the skill worse than useless.
- **No rebuttal round in `plan`/`gap`.** Corroboration is a bonus, not a requirement.
- **One rebuttal round in `quality`, conflicts only.** Never loop.
- **Report, then stop.** The user decides what gets fixed.
- **Reviewers stay read-only.** Never lift `--workdir`'s hardening; never give a reviewer a
  writable path. If code must change, you change it.
- **Say who dropped out.** Two reviewers is a valid run; a silent dropout is not.

## When to suggest

- The user finished a plan or a non-trivial diff and is about to move on.
- The user has been in a long session and may have lost track of earlier constraints.
- A change touches many call sites, or the user says "I think that's everything".
- Not for routine quality polish — the built-in `/simplify` and `/code-review` cover that with
  one model. Reach for `quality` mode when there is a real architectural trade-off in dispute.
