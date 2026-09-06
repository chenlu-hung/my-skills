---
name: llm-council
description: Convenes a multi-model "council" to answer a question, then synthesizes a single best answer — inspired by Karpathy's llm-council. Each member runs through its own subscription/sign-in CLI, not an API key: ChatGPT (the desktop app, via `chatgpt-ask`), Gemini (Antigravity `agy`), and Claude (`claude -p`), with Codex (`codex exec`) standing in where the desktop app cannot reach. This Claude Code session chairs the synthesis. Use when the user says "ask the council", "llm council", "convene the council", "second opinion", "what do other models think", "compare models on this", "ask codex and gemini too", "make the models debate", "have them cross-examine each other", or invokes "/llm-council".
argument-hint: "\"<question>\" | debate \"<question>\" | quick \"<question>\" | raw \"<question>\""
---

# LLM Council

Answer a question by polling several frontier models, having them critique each
other **anonymously**, then synthesizing one authoritative answer. Mirrors
[karpathy/llm-council](https://github.com/karpathy/llm-council)'s three stages, but
every member is reached through its **own subscription CLI** — no API keys, no
OpenRouter.

## Members

| Member | Reached via | Auth (subscription / sign-in, not API key) | On the default roster |
|---|---|---|---|
| **ChatGPT** | `chatgpt-ask`, over the desktop app's debugging port | the app's own sign-in | yes |
| **Gemini** | Antigravity `agy -p` | Google Antigravity sign-in (Gemini models) | yes |
| **Claude** | `claude -p` | Claude subscription — runs as an **independent member**, isolated from the chair | yes |
| **Codex** | `codex exec` | ChatGPT subscription (`auth_mode: chatgpt` in `~/.codex/auth.json`) | only as ChatGPT's stand-in |
| **opencode** | `opencode run` | opencode's own free tier — costs no subscription quota | no — name it in `--members` |

**GPT sits on the council through the desktop app, not Codex.** For a council answer — prose
from a self-contained prompt, no repo, no tool calls — `chatgpt` and `codex` are the same
voice off a different meter, and the app's meter is the ChatGPT **conversation** allowance
instead of Codex quota. So `chatgpt` holds the seat and `codex` is kept for the two kinds of
run the app cannot serve. `council.py` makes that swap itself and announces it on stderr:

- **`--workdir`** — the app answers from inside a GUI and has no filesystem, so a borrowed
  repo is invisible to it.
- **an explicit `--output-schema`** — a GUI has no such flag, so the schema is appended to the
  prompt as a request. The default answer schema is fine that way (it is rendered back to
  prose regardless, and a member that ignores it still contributes), but a caller who names
  its own schema is reading `structured` downstream and needs the contract kept.

The swap rewrites the **default** roster only. A roster given in `--members` is used exactly
as written — `chatgpt` then refuses `--workdir` itself rather than being quietly answered by
a member nobody asked for.

**opencode is off the default roster.** Its free models sit a rung below the rest, and a weak
answer costs a council more than a missing one: it still gets ranked, still gets synthesized,
still takes a reviewer's attention in Stage 2. Add it back with `--members` when a fourth
voice is worth more than its quality. Its free slugs also rotate, and a withdrawn one fails
every call rather than falling back — `opencode models | grep free` lists the live ones, and
the default is `DEFAULT_OPENCODE_MODEL`. It is refused on `--workdir` too: unlike the others
it has no read-only mode to hold it to.

Members run **in parallel** through `council.py`, each in a throwaway temp dir —
this skill never passes `--workdir`, so no member can see the user's repo (see Rules).
**This Claude Code session is the Chairman**: it only synthesizes — it does *not* also submit
a member answer, because the `claude` member already carries Claude's independent voice (run
with `--setting-sources project` so the session's hooks/memory don't leak into it). The CLIs
are stateless one-shot calls, so every prompt must be self-contained. `council.py` defaults to
**`chatgpt,gemini,claude`** (`DEFAULT_MEMBERS`); pass `--members` to change that. Stage 2 names
its reviewers explicitly rather than taking this default — see Stage 2.

## The ChatGPT member

Runs through the **`chatgpt-ask`** command, which lives in its own directory
(`chatgpt-bridge/`) because this skill is not its only consumer — see that README for
install and behaviour. `council.py` resolves it on PATH first and falls back to the
sibling checkout, so it is never copied in here.

**Nothing needs setting up.** The bridge relaunches ChatGPT.app with a debugging port when
one isn't already open, and quits it again afterwards — an app that was *already* serving
the port belongs to the user's session and is left alone. Budget about 15s for a cold app
start on top of the answer itself.

**Questions go into a temporary chat**, so they never enter the account's history. If that
control can't be found the member *refuses* rather than filing the thread for real;
`--allow-history` overrides that if the user asks for it.

It honours `--output-schema` the same way `opencode` does, but by *asking* rather than enforcing:
there is no `--output-schema` in a GUI, so the schema is appended to the prompt and
`parse_structured` falls back to prose if the reply is not JSON.

Three limits the CLI members don't have:

- **No filesystem.** It answers from inside the app, so `--workdir` is unreadable to it. The
  member refuses outright rather than answering as though it had read the repo.
- **It drives the real UI.** Don't use the app while a call is in flight, and note the
  debugging port is unauthenticated for as long as it is open.
- **Selectors are version-bound.** It finds the composer and the reply by DOM shape
  (`[contenteditable]`, `[data-user-message-bubble]`, `_MarkdownRoot_*`). An app update that
  reshapes those breaks it; re-probe the DOM rather than guessing new selectors.

## Modes

| Invocation | Stages | Use when |
|---|---|---|
| `/llm-council "<q>"` | 1 → 2 → 3 (full) | High-stakes / contentious — you want cross-review before synthesis |
| `/llm-council debate "<q>"` | 1 → 2 → **2.5 (conditional rebuttal)** → 3 | The models actually *disagree* and you want them to defend or concede before synthesis |
| `/llm-council quick "<q>"` | 1 → 3 (skip cross-review) | Want multiple views fast and cheap |
| `/llm-council raw "<q>"` | 1 only | Just show each model's answer side by side, no synthesis |

> Stage 1 is one parallel `council.py` call (~10–60s depending on the slowest model);
> Stage 2 is one call **per reviewer**, run in parallel, so it costs about the same wall
> clock as one. A full run typically takes one to two minutes; `debate` adds at most one
> more round (~30–60s) and **only when the cross-review actually surfaced disagreement** —
> tell the user up front.

## Workflow

### Stage 1 — First opinions (fan-out)

**First, write the scoring criteria — before you read a single answer.** Derive three short,
independent, question-specific criteria from the question alone and write one per line to
`<tmp>/criteria.txt`. Once you have read the answers you cannot write an uncontaminated
rubric: you would be choosing the yardstick to fit answers you have already formed an
opinion about. A fixed triple is the wrong rubric for most questions anyway — "depth" means
nothing for a factual lookup. If you cannot derive good ones, omit the file and
`council.py` falls back to `correctness, depth, usefulness`; never block Stage 2 on this.

Then write the question to a temp file and dispatch **all** members in parallel, saving the
JSON to a file (Stage 2's anonymizer reads it from disk):
```sh
python3 ~/.claude/skills/llm-council/council.py --prompt-file <tmp>/q.txt > <tmp>/stage1.json
```
Check `members.<name>.ok` in the JSON. If a member has `ok: false`, note who dropped out
(e.g. CLI not installed / not signed in) and continue with whoever answered. **If *every*
member failed, stop**: report the dropouts and say there is no council answer. Do not
synthesize one from nothing — a council of zero is not a small council. You do **not**
add your own answer here — the `claude` member already represents Claude independently.

You now hold one answer per member.

### Stage 2 — Cross-review & ranking (skip in `quick`)

1. **Anonymize with the script — never shuffle or relabel by hand:**
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --anonymize <tmp>/stage1.json \
       --question-file <tmp>/q.txt --members codex,claude --criteria <tmp>/criteria.txt
   ```
   It labels the usable answers `Response A / B / C / …` and writes, next to `stage1.json`,
   **one prompt per reviewer** (`review_prompt.codex.txt`, `review_prompt.claude.txt`, …)
   plus `label_map.json`, which is now nested: `{reviewer: {label: member}}`. The stdout
   JSON reports the prompt path for each reviewer under `review_prompts`. With fewer than 2
   usable answers it refuses and tells you what to do instead.

   **Each reviewer gets a different ordering, and that is the point.** Anonymising strips
   brand bias, but one shared order leaves position bias *correlated*: if every reviewer
   sees the same Response A, whatever primacy/recency preference the models have in common
   adds up across the council instead of cancelling. The orders are cyclic rotations of one
   shuffle, not independent shuffles — two independent shuffles of two answers coincide
   half the time, which is exactly the small-council case this has to survive.

   `--members` names the **reviewers**, who need not be the members that answered: a
   reviewer absent from Stage 1 still gets a prompt, and an answer from a member that is
   not a reviewer is still reviewed. Pass it explicitly; the roster is a decision, not a
   default.
   **Never include `label_map.json` — or any member name — in anything sent to a member.**
   That, not chair ignorance, is the guarantee: you hold `stage1.json` and can always
   identify an author from its text, and `debate` mode *requires* you to, since Stage 2.5
   routes each rebuttal back to its own author. Keep the mapping out of every outgoing
   prompt; open the file itself only when you need it (Stage 2.5 routing, or Stage 3).
2. Dispatch **one call per reviewer**, each with its own prompt and its own output file,
   run in parallel:
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --members codex \
       --output-schema ~/.claude/skills/llm-council/schema/review.schema.json \
       --prompt-file <tmp>/review_prompt.codex.txt > <tmp>/stage2.codex.json
   python3 ~/.claude/skills/llm-council/council.py --members claude \
       --output-schema ~/.claude/skills/llm-council/schema/review.schema.json \
       --prompt-file <tmp>/review_prompt.claude.txt > <tmp>/stage2.claude.json
   ```
   **The output file names matter**: `--aggregate` looks for `stage2.<reviewer>.json` beside
   `label_map.json`.
   **A shared redirect would interleave the JSON and lose rankings** — one output file per
   reviewer, always. Check `members.<reviewer>.ok` in each; a reviewer that errored is a
   Stage-2 dropout and must be named in the council notes, exactly like a Stage-1 one. If
   **no** reviewer returned a usable ranking, skip to Stage 3 and synthesize from the
   Stage-1 answers alone, saying that cross-review did not run.

   The roster is a deliberate pair — `codex` (gpt-5.6-sol) and `claude` (claude-opus-5) —
   chosen because ranking answers well is harder than producing them and the remaining
   members are not strong enough at it.

3. **Aggregate — do not do this arithmetic by hand:**
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --aggregate <tmp>/label_map.json \
       --criteria <tmp>/criteria.txt
   ```
   Pass the **same** `--criteria` file as step 1; the names are validated back. It emits
   `ranking`, per-response `mean` / `spread` / `per_criterion`, `factual_errors`, a
   `reviewers` map saying who counted and who did not, and the `gate` that Stage 2.5 reads.

   Reviewers scored *labels*, and a label means a different answer to each of them, so the
   numbers are not comparable until this step maps them back. It also **drops each
   reviewer's score for its own answer** — a reviewer grading itself favours itself, and
   with a two-reviewer roster that bias no longer averages out. The resulting unequal
   comparison counts are handled by the `w/c` normalisation, which is what that
   normalisation is for.

   Consequences worth knowing when you read the output: a reviewer's own answer is scored
   by everyone *except* itself, so on a two-reviewer council it carries one score and its
   `spread` is `null`. Only answers from non-reviewer members get a disagreement signal.
   A reviewer that failed, ignored the schema, or produced unusable scores appears in
   `reviewers` with a reason and is excluded from the arithmetic while its prose stays
   available in its `stage2.<reviewer>.json`. If **no** reviewer was usable the command
   exits non-zero: synthesize from the Stage-1 answers and say cross-review did not run.

You now hold one aggregate over the answers themselves, not per-reviewer label soup.

### Stage 2.5 — Conditional cross-examination (`debate` only)

The point of this stage is **one targeted rebuttal round, fired only when it would
change anything** — not a free-for-all that grinds the answers into mush. Open-ended
questions are exactly where extra debate rounds make models converge toward whoever
sounds most confident rather than whoever is right, so this stays surgical.

1. **Gate — read `gate` from the aggregate; do not re-derive it in prose.**
   `gate.rebuttal_recommended` is true when either arm trips:
   - **the numeric arm** — the top two answers are closer together than the reviewers
     disagree about them (`top_two_gap` below the larger `spread` of the two). A near-tie
     the reviewers agree on is settled; a near-tie they disagree about is not.
     `gate.contested_criterion` names the criterion they disagree about most, when there is
     enough data to say.
   - **the qualitative arm** — a reviewer quoted a specific factual error, listed under
     `factual_errors`. This arm stands alone and is never overridden by the numbers: an
     allegation of a false claim deserves an answer whatever the scores say.

   If `rebuttal_recommended` is false, **skip this stage**, say so in one line ("council was
   in consensus; no rebuttal round needed"), and go to Stage 3. Do not manufacture a debate.
   When the numeric arm cannot run — fewer than two reviewers scored the leaders — the gate
   says so in `reasons`; fall back to the qualitative arm alone rather than guessing.

2. **One rebuttal round (contested answers only).** For each answer that drew a real
   objection, send it *back to its own author* with the strongest objection(s) raised
   against it (quoted from the cross-review, kept anonymous — the author never learns who
   objected). Dispatch one `council.py` call per contested author so each prompt stays
   self-contained:
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --members <author> \
       --prompt-file <tmp>/rebuttal_<author>.txt > <tmp>/rebuttal_<author>.json
   ```
   **Give every call its own output file.** These run in parallel, so a shared redirect
   interleaves their JSON and loses answers.
   Rebuttal prompt shape:
   ```
   Question: <original question>

   This was your answer:
   <that member's Stage-1 answer>

   A reviewer raised this objection to it:
   <the strongest objection(s), verbatim, anonymized>

   Defend your answer with concrete reasoning or evidence, OR concede the specific
   point if the objection is correct. Be specific — do not restate your original
   answer. If you concede, say exactly what changes.
   ```
   These calls are independent — run them in parallel (e.g. background) but it is still
   **one round**. Do not feed the rebuttals back for a second round.

You now hold, for each contested answer, a defend-or-concede response.

### Stage 3 — Chairman synthesis (you)

The aggregate from Stage 2 step 3 has already de-anonymized everything: `ranking` and
`responses` are keyed by member. Read it rather than mapping labels yourself — `Response A`
means a different answer to each reviewer, and doing that bookkeeping by eye is how a
ranking silently comes out wrong. The per-reviewer prose in each `stage2.<reviewer>.json`
is still worth reading for the *reasons*; the numbers come from the aggregate.
In `quick` mode, and whenever cross-review did not run, there is no aggregate at all —
synthesize from Stage 1 and report no ranking rather than inventing one. Then, as
**Chairman**, write the final answer. You are *not* a contestant
— weigh the rankings and the substance honestly and adopt any member's point when it's stronger;
don't favour the `claude` member by default. In `debate` mode also weigh the Stage-2.5 round:
a **conceded** point is settled (drop it from the answer), and a point that was **defended with
specifics** against a weak objection stands — surface which disputes resolved and which stayed
genuinely open. Present:

1. **The answer** — one synthesized, authoritative response (this is the headline).
2. **Council notes** (compact, secondary): each member's one-line stance, the aggregate
   ranking, and any real disagreement worth flagging. Give the ranking with its numbers
   (`mean` per response) rather than as a bare order, and name any reviewer that was
   excluded from the arithmetic and why. In `debate` mode add a one-line verdict per
   contested point (defended / conceded / still open). Keep it short.

For `raw` mode, stop after Stage 1 and show the answers side by side. For `quick`, skip
Stage 2 and synthesize directly from the Stage-1 answers. For `debate`, run the conditional
Stage 2.5 before synthesizing.

## Requirements

Each member is optional — if its CLI is absent or signed out, `council.py` returns that member
as `ok: false` and the council proceeds with the rest.

- **`chatgpt-ask`** (plus ChatGPT.app and `uv`) — on the default roster, so a plain run needs
  it. The app is launched and quit for you, so it does not need to be running beforehand. If
  the command is missing, that member returns `ok: false` telling you to link it; see
  `chatgpt-bridge/`. macOS only.
- **`agy`** (Antigravity CLI) — signed in for Gemini models.
- **`claude`** (Claude Code) — the same subscription as this session.
- **`codex`** — signed into a ChatGPT subscription. Verify `~/.codex/auth.json` has
  `"auth_mode": "chatgpt"`; else `codex login`. Not on the default roster, but a Stage-2
  reviewer and ChatGPT's stand-in on `--workdir`, so a full run still reaches it.
- **`python3`** (stdlib only — the `chatgpt` member's own dependency is handled by `uv`).

`council.py` degrades gracefully: a missing CLI, timeout, or crash becomes a per-member
`ok: false` with an `error` string rather than failing the whole run.

### CLI wiring that is easy to get wrong

- **Every CLI child gets `stdin=DEVNULL`.** With stdin a pipe rather than a TTY — which is
  the case for every call from an agent harness — `codex exec` prints
  `Reading additional input from stdin...` and waits for a SECOND prompt. The turn is then
  truncated: no `-o` file, one status line on stdout, and `codex doctor` reports everything
  healthy, so it looks like a model or quota failure when it is neither. `agy` leaks the
  same way. Pinned by `test_council.py` → "stdin isolation".
  Because members run concurrently, whichever child reads the inherited pipe first drains
  it — so the leak is nondeterministic. Test one member at a time.
- **`codex` reports through `-o <file>`, never stdout.** Do not parse its prose output.
- **Recovery when a codex answer comes back empty:** the full turn is always persisted to
  `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. `codex_rollout_fallback()` reads the
  newest rollout touched since the call began and takes the last assistant `output_text`.
  This has recovered complete answers that stdout lost entirely.
- **`--model gpt-5-codex` is rejected under ChatGPT auth** (HTTP 400). Leave the model empty
  to take the subscription default.
- **`agy`: flags must precede `-p`** (Go flag parsing), or `-p` swallows the next flag as its
  value. Headless `agy` also needs `--dangerously-skip-permissions` just to *read* files —
  never point that at a directory you are not willing to have written to; copy files to a
  scratch dir first if the target matters. Long prompts can still return empty.
- **`opencode run` needs `--format json`**; its plain output is one banner line under a pipe.

### Structured output is ON by default

Every member is bound to `schema/answer.schema.json` (`answer`, `key_points`, `confidence`,
`caveats`), so members stay comparable. **Three CLIs enforce it; two only get asked:**

| member | flag | shape |
|---|---|---|
| codex | `--output-schema <FILE>` | the `-o` file holds raw JSON |
| agy | `--json-schema <FILE>` **plus `--output-format json`** — it refuses the schema otherwise | envelope, parsed object under `structured_output` |
| claude | `--json-schema '<inline JSON>'` — **a path is rejected** | stdout is raw JSON |
| opencode | *none — no `--output-schema` equivalent exists* | the schema is appended to the prompt; conformance is voluntary |
| chatgpt | *none — a GUI has no such flag* | same: appended to the prompt, prose accepted back |

`chatgpt` is on the default roster, so a default run already contains one member whose
structure is asked for rather than enforced (an explicit `--output-schema` is exactly what
swaps that member for `codex`). `parse_structured()` does `json.loads` and no validation, which is
harmless while the fields are prose — but anything that does *arithmetic* on member output
must validate locally first.

**The JSON is rendered straight back to Markdown into `answer`, with the raw object kept under
`structured`.** That is what makes the default safe: Stage-3 synthesis, `--anonymize`, and the
human all keep reading prose. A member that ignores the schema passes through unchanged rather
than being dropped.

`schema/review.schema.json` is the second schema in the box: Stage 2 binds it so each
reviewer returns a per-response, per-criterion score vector plus any quoted factual errors,
which is what `--aggregate` consumes. Scoring each criterion independently beats one
compound judgement — asked "is this correct?" as a single question, a verifier latches onto
whichever factor is most salient in the prompt.

`--output-schema FILE` swaps the schema (e.g. a findings shape for `review-me`);
`--no-output-schema` turns it off entirely.

**Two constraints the schema must satisfy** — both found by end-to-end testing, both silent
until the member returns an error string instead of an answer:

1. **No `$schema` key.** `claude --json-schema` rejects a `https://json-schema.org/draft/2020-12/schema`
   ref outright ("no schema with key or ref").
2. **`required` must list EVERY key in `properties`**, with `additionalProperties: false`.
   codex goes through OpenAI structured outputs, which refuses partial `required`
   ("'required' is required to be supplied and to be an array including every key in properties").
   So there are no optional fields — document "empty array if none" in each description instead.

## Rules

- **Anonymity is the point.** The `--anonymize` mode owns the orderings and the labels —
  never rebuild a review prompt by hand and never leak the A/B/C → member mapping (or any
  member name) into a member's prompt. That leak is the thing the rule forbids; the chair
  reading the map is not, and `debate` mode requires it before Stage 3. Stripping brand bias
  is why the labels exist; giving each reviewer a different ordering is why the position
  bias does not survive either.
- **Members only via `council.py`** — it runs each in a throwaway temp dir (codex additionally
  in a read-only sandbox; `claude` with `--setting-sources project`) so they can't touch the
  user's repo or inherit this session's hooks/memory while answering.
- **Never pass `--workdir` from this skill.** That flag points members at a real directory so
  they can read it; it exists for the `review-me` skill, where the answer depends on code the
  prompt cannot carry. A council answer must come from the prompt alone — giving one member
  repo access it did not need would make the answers incomparable and break the ranking.
- **Self-contained prompts.** The CLIs keep no memory between calls, so each stage's prompt
  must carry everything it needs (the question, and the answers to review).
- **`debate` stays surgical.** Stage 2.5 is *one* round and *only* runs on substantive
  disagreement (conflicting rankings or a flagged correctness dispute). Never loop it, never
  run it on a consensus council — extra rounds on open-ended questions homogenize answers
  toward the most confident voice, which is the opposite of what the council is for.
- **Don't rig the synthesis** toward the `claude` member. The Chairman is a neutral aggregator,
  not a contestant — no self-promotion.
- Surface dropouts: if a member errored, say so in the council notes — a smaller council
  (even two members) is still valid.

## When to suggest

- The question is open-ended, high-stakes, or contentious and one model's take isn't enough.
- The user says "second opinion", "ask the council", "what would other models say", or wants
  Codex/Gemini in the loop alongside Claude.
- A previous single-model answer was disputed — offer to convene the council to adjudicate.
