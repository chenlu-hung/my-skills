---
name: fable-advisor
description: Formalizes the Anthropic-recommended architect/implementer split — the strongest model (Fable 5, this session) grills the user via the grill-me skill, designs the architecture, and writes it to a self-contained doc; an implementer subagent (default Opus, overridable to sonnet/haiku via argument) then implements against that doc and consults the Fable advisor whenever it hits a design-level block; Fable reviews the final diff against the doc before closing. Use when the user says "fable advisor", "architect then implement", "design then hand to sonnet", "Fable 設計 sonnet 實作", "烤完設計架構交給 sonnet", "當我的架構顧問", or invokes /fable-advisor (optionally with a model argument, e.g. "/fable-advisor opus").
---

# Fable Advisor — architect / implementer split

Pattern: the strongest model interrogates the plan, designs the architecture, and stays on as **advisor**; an implementer model (default **Opus**) does the implementation against the written architecture doc, consulting the advisor on design-level blocks; the advisor reviews the result before closing.

**Role check (non-skippable):** this session is the Architect/Advisor. If the session model is not the strongest available (e.g. already Sonnet or Haiku), say so in one line and ask whether to continue anyway — the pattern's value is the capability gap between architect and implementer.

## Phase 1 — Grill

Invoke the **`grill-me` skill** and run **its Phase 1 only** (the interview). Stop before its Phase 2/3 — the plan file it writes feeds Phase 2 here instead of grill-me's own task list and dispatch.

Exit condition: every load-bearing branch resolved and logged in the plan file grill-me started (`docs/plans/<YYYY-MM-DD>-<slug>.md`).

## Phase 2 — Architect

Design the architecture from the decision log and write it into that same plan file. The file is the **single source of truth** — the implementer never sees this conversation. Never start a second doc.

- Path: the plan file from Phase 1 (if the user names another path, move the file there).
- Content: fill the **plan doc template** in [templates.md](templates.md) — add every section around grill-me's `## Decisions`, `## Terms` and `## Open`, which stay as written.
- Each `PROTOTYPE` item in `## Open` becomes a task named `PROTOTYPE <what to build>`, ordered first; tasks under its branch are `blocked by` it and get specified after the user reacts (Phase 4).
- **Self-containment test** before handoff: could a fresh session with no access to this conversation implement from the doc alone? If any task still needs conversation context, the doc is not done — bake that context in verbatim.

Show the user the doc path plus a ≤10-line summary and **stop until they approve** — the same checkpoint as grill-me's Phase 2: is the granularity right, is each task blocked only by what genuinely gates it, should anything merge or split? Apply their changes to the file and ask again until they approve; the approval covers the handoff.

## Phase 3 — Handoff to the implementer

**Implementer model (parameter):** default `opus`. Override when the user passes it as an argument (`/fable-advisor sonnet`) or says so in the request ("用 sonnet 實作", "implement with haiku") — accepted values are the Agent tool's model names: `sonnet`, `opus`, `haiku`. If the user names anything else, ask once instead of guessing. `fable` is not a valid implementer — it collapses the architect/implementer capability gap; if asked, point that out and suggest skipping this skill and implementing directly. Announce the choice in one line ("implementer: opus (user override)") before spawning.

Spawn the implementer with the **Agent tool**: `subagent_type: "general-purpose"`, `model: <implementer model>`, prompt = the **implementer brief template** in [templates.md](templates.md) with the blanks filled (the brief embeds the consult protocol). Background run is fine — completion re-invokes you.

Parallelism rule (explicit, no judgment needed): one implementer by default; spawn parallel implementers **only** when the plan file's `## Tasks` contains ≥2 tasks whose `files:` sets are disjoint, and then give each `isolation: "worktree"`.

## Phase 4 — Advisor loop

Each implementer run ends in either `DONE` or `CONSULT` (formats in templates.md). On `CONSULT`:

1. Rule decisively — decision + why + what it forecloses, like a grill-me log line. Answer the design question; **don't write the code for it**.
2. If the ruling *changes* the architecture, update the plan file **first** — the affected `## Decisions` line plus every section it touches — then reply. The doc must never lag the decisions.
3. A consult on a finished `PROTOTYPE` task: show the user the prototype and wait for their reaction. Log it as a new `## Decisions` line, specify the tasks it blocks, then rule.
4. Reply with **SendMessage** to the same agent so its context survives. Never spawn a fresh agent to continue an existing task.
5. Escalation rule (explicit): if the **same task** comes back a 3rd time, or the implementer reports a failed attempt on it twice, take that task over yourself (set its `owner: advisor`) and tell the implementer to skip to the next one.

Relay each consult and your ruling to the user in 1–2 lines as it happens.

## Phase 5 — Review & close

Never accept `DONE` blind:

1. Read the full diff against the plan file, task by task — is each "done when" criterion met?
2. Run the verification commands named in the doc's `## Verification` yourself. Optionally run the `code-review` skill on the diff.
3. Small deviations (naming, style, a missed edge case): fix yourself. Architecture-level deviations: send back via SendMessage, citing the doc section violated.
4. Check off each task in `## Tasks` as it passes: `- [x] T3 … — verified: <real result line>`.

Close with a per-task summary: what changed, verification result, consults resolved, anything you fixed or rejected. Leave the plan file in the repo — it is the record.
