---
name: llm-council
description: Convenes a multi-model "council" to answer a question, then synthesizes a single best answer — inspired by Karpathy's llm-council. Each member runs through its own subscription/sign-in CLI, not an API key: Codex (ChatGPT sub), Gemini (Antigravity `agy`), Claude (`claude -p`), and DeepSeek (opencode, free). This Claude Code session chairs the synthesis. Use when the user says "ask the council", "llm council", "convene the council", "second opinion", "what do other models think", "compare models on this", "ask codex and gemini too", "make the models debate", "have them cross-examine each other", or invokes "/llm-council".
argument-hint: "\"<question>\" | debate \"<question>\" | quick \"<question>\" | raw \"<question>\""
---

# LLM Council

Answer a question by polling several frontier models, having them critique each
other **anonymously**, then synthesizing one authoritative answer. Mirrors
[karpathy/llm-council](https://github.com/karpathy/llm-council)'s three stages, but
every member is reached through its **own subscription CLI** — no API keys, no
OpenRouter.

## Members

| Member | Reached via | Auth (subscription / sign-in, not API key) |
|---|---|---|
| **Codex** | `codex exec` | ChatGPT subscription (`auth_mode: chatgpt` in `~/.codex/auth.json`) |
| **Gemini** | Antigravity `agy -p` | Google Antigravity sign-in (Gemini models) |
| **Claude** | `claude -p` | Claude subscription — runs as an **independent member**, isolated from the chair |
| **DeepSeek** | opencode `run` | opencode sign-in — default `opencode/deepseek-v4-flash-free` (free) |

All four members run **in parallel** through `council.py` (each in a throwaway temp dir).
**This Claude Code session is the Chairman**: it only synthesizes — it does *not* also submit
a member answer, because the `claude` member already carries Claude's independent voice (run
with `--setting-sources project` so the session's hooks/memory don't leak into it). The CLIs
are stateless one-shot calls, so every prompt must be self-contained. `council.py` defaults to
all four members; pass `--members` to use a subset.

## Modes

| Invocation | Stages | Use when |
|---|---|---|
| `/llm-council "<q>"` | 1 → 2 → 3 (full) | High-stakes / contentious — you want cross-review before synthesis |
| `/llm-council debate "<q>"` | 1 → 2 → **2.5 (conditional rebuttal)** → 3 | The models actually *disagree* and you want them to defend or concede before synthesis |
| `/llm-council quick "<q>"` | 1 → 3 (skip cross-review) | Want multiple views fast and cheap |
| `/llm-council raw "<q>"` | 1 only | Just show each model's answer side by side, no synthesis |

> Each stage is one parallel `council.py` call (~10–60s depending on the slowest model).
> A full run typically takes one to two minutes; `debate` adds at most one more round
> (~30–60s) and **only when the cross-review actually surfaced disagreement** — tell the
> user up front.

## Workflow

### Stage 1 — First opinions (fan-out)

Write the question to a temp file, then dispatch **all** members in parallel:
```sh
python3 ~/.claude/skills/llm-council/council.py --prompt-file <q.txt>
```
Parse the JSON (`members.<name>.answer`). If a member has `ok: false`, note who dropped out
(e.g. CLI not installed / not signed in) and continue with whoever answered. You do **not**
add your own answer here — the `claude` member already represents Claude independently.

You now hold one answer per member.

### Stage 2 — Cross-review & ranking (skip in `quick`)

1. **Anonymize**: shuffle the answers and relabel them `Response A / B / C / …` (one label per
   member that answered). Keep the label→member mapping private — reviewers must never know
   which model wrote which answer.
2. Build a self-contained review prompt and dispatch it to the members:
   ```
   Question: <original question>

   Below are the anonymous responses. Evaluate each for correctness, depth, and
   usefulness, then rank them best-to-worst with a one-line justification each.

   --- Response A ---
   <answer>
   --- Response B ---
   <answer>
   --- Response C ---
   <answer>
   ... (one block per member)
   ```
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --prompt-file <review.txt>
   ```

You now hold one ranking per member, all over the same anonymized set.

### Stage 2.5 — Conditional cross-examination (`debate` only)

The point of this stage is **one targeted rebuttal round, fired only when it would
change anything** — not a free-for-all that grinds the answers into mush. Open-ended
questions are exactly where extra debate rounds make models converge toward whoever
sounds most confident rather than whoever is right, so this stays surgical.

1. **Gate — is there substantive disagreement?** From the Stage-2 rankings, check for
   *either*:
   - the rankings **conflict** (members put different answers at the top, not just reorder
     the middle), or
   - a reviewer flagged a **correctness/factual dispute** about a specific answer (not a
     style or "I'd phrase it differently" preference).

   If neither holds — the council substantively **agrees** — **skip this stage**, say so in
   one line ("council was in consensus; no rebuttal round needed"), and go straight to
   Stage 3. Do not manufacture a debate.

2. **One rebuttal round (contested answers only).** For each answer that drew a real
   objection, send it *back to its own author* with the strongest objection(s) raised
   against it (quoted from the cross-review, kept anonymous — the author never learns who
   objected). Dispatch one `council.py` call per contested author so each prompt stays
   self-contained:
   ```sh
   python3 ~/.claude/skills/llm-council/council.py --members <author> --prompt-file <rebuttal.txt>
   ```
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

De-anonymize privately, then as **Chairman** write the final answer. You are *not* a contestant
— weigh the rankings and the substance honestly and adopt any member's point when it's stronger;
don't favour the `claude` member by default. In `debate` mode also weigh the Stage-2.5 round:
a **conceded** point is settled (drop it from the answer), and a point that was **defended with
specifics** against a weak objection stands — surface which disputes resolved and which stayed
genuinely open. Present:

1. **The answer** — one synthesized, authoritative response (this is the headline).
2. **Council notes** (compact, secondary): each member's one-line stance, the aggregate
   ranking, and any real disagreement worth flagging. In `debate` mode add a one-line
   verdict per contested point (defended / conceded / still open). Keep it short.

For `raw` mode, stop after Stage 1 and show the answers side by side. For `quick`, skip
Stage 2 and synthesize directly from the Stage-1 answers. For `debate`, run the conditional
Stage 2.5 before synthesizing.

## Requirements

Each member is optional — if its CLI is absent or signed out, `council.py` returns that member
as `ok: false` and the council proceeds with the rest.

- **`codex`** — signed into a ChatGPT subscription. Verify `~/.codex/auth.json` has
  `"auth_mode": "chatgpt"`; else `codex login`.
- **`agy`** (Antigravity CLI) — signed in for Gemini models.
- **`claude`** (Claude Code) — the same subscription as this session.
- **`opencode`** — signed in (`opencode auth`); the default DeepSeek V4 Flash model is free.
- **`python3`** (stdlib only).

`council.py` degrades gracefully: a missing CLI, timeout, or crash becomes a per-member
`ok: false` with an `error` string rather than failing the whole run.

## Rules

- **Anonymity is the point.** Never leak the A/B/C → member mapping into a reviewer's prompt;
  it exists to strip brand bias from the rankings.
- **Members only via `council.py`** — it runs each in a throwaway temp dir (codex additionally
  in a read-only sandbox; `claude` with `--setting-sources project`) so they can't touch the
  user's repo or inherit this session's hooks/memory while answering.
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
