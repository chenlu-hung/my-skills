# fable-advisor templates

Literal templates. Fill every `<blank>`; delete nothing. If a section truly has no content, write `none` — an empty section means the doc is unfinished.

## 1. Plan doc template

One file per plan: `docs/plans/<YYYY-MM-DD>-<slug>.md`. grill-me's Phase 1 starts it with `## Decisions`, `## Terms` and `## Open`; in Phase 2 the advisor adds every other section around those three, in this order. The three grill-me sections stay as written — never copy them into another doc or rephrase them.

```markdown
# <plan>

> Author: Fable 5 advisor session, <date>. Implementer: <implementer model>.
> This doc is the single source of truth. Amendments arrive as advisor rulings and are folded back in here.

## Goal
<2–3 sentences: what exists when this is done, and for whom>

## Decisions
<from grill-me, as written; rulings that change a decision edit its line here>
#1 <decision>: <answer> (implies: <what this forecloses or forces>)
#2 ...

## Terms
<from grill-me, as written>
<Term>: <what it is> (avoid: <rejected synonyms>)

## Open
<from grill-me, as written; every PROTOTYPE item here has a PROTOTYPE task under ## Tasks>

## Non-goals
<what is explicitly out of scope, so the implementer doesn't "helpfully" add it>

## Component map
| Component | File(s) | Responsibility | New / changed |
|---|---|---|---|

## Data & control flow
<short prose or ASCII diagram: who calls whom, what data moves where>

## Tasks
- [ ] T1 <task> — files: <paths> — owner: implementer — blocked by: <T#, … | none> — done when: <specific test passes / observable behavior>
- [ ] T2 ...

## Conventions & constraints
<repo idioms to match, naming, error-handling style, libraries allowed/forbidden, anything the codebase can't teach fast>

## Verification
<exact commands to run and what must pass; include how to run the test suite in this repo>

## Consult triggers
The implementer stops and consults (protocol in its brief) when:
- the doc is ambiguous or contradicts the actual code;
- a decision not covered here would change structure, public API, data model, or dependencies;
- a doc-mandated approach fails after one honest attempt;
- a "done when" criterion cannot be met as specified;
- a PROTOTYPE task is done — the user reacts to it before the tasks it blocks are specified.
Everything else — local naming, test phrasing, obvious idioms — the implementer decides alone.
```

## 2. Implementer brief (Agent tool prompt)

Spawn with `subagent_type: "general-purpose"`, `model: <implementer model>` (default `opus`; see SKILL.md Phase 3 for the override rule).

```text
You are the implementer for <feature>, working in <absolute repo path>.

Read <absolute plan doc path> in full before touching anything. It is the single
source of truth; this brief adds process only, never design.

Rules:
1. Implement the tasks under "## Tasks", in order, respecting "blocked by".
2. Name things as "## Terms" defines them; never use a word it lists under "avoid".
3. Decide yourself: anything the doc settles, plus mechanical choices (local
   naming, test phrasing, matching existing idioms).
4. When any condition under "## Consult triggers" holds, do NOT guess — consult.
   To consult, end your run with exactly this block and nothing after it:

   CONSULT
   task: <T#>
   question: <one sentence>
   context: <files touched, what you tried or found>
   options: A) <option> B) <option> — my lean: <A/B and one-line why>

   The ruling arrives as your next message. Apply it as an amendment to the doc.
5. Never mark a task done without actually running its "done when" criterion;
   record the real output, not a paraphrase.
6. When every task passes, end your run with exactly:

   DONE
   T1: <what changed> — verification: <verbatim result line>
   T2: ...
   deviations from the doc: <list, or "none">

7. If a task is impossible after a consult ruling, report it under DONE as
   "T#: BLOCKED — <why>" rather than silently skipping it.
```

## 3. Advisor ruling format (SendMessage reply to a CONSULT)

```text
RULING on <T#>: <decision in one sentence>
why: <one line>
implies: <what this forecloses or forces downstream>
doc: <"unchanged" | "updated ## <section> — re-read it">
Continue from <T#>.
```
