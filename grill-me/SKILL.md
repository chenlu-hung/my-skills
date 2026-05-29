---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies one at a time — settle upstream decisions before the ones that depend on them, so we never revisit a closed branch. For each question, give your recommended answer and a one-line why.

Ask one question at a time. When a question has discrete options, ask it with the AskUserQuestion tool and put your recommendation first, labeled "(Recommended)"; use plain text for open-ended "why"/"what-if" probing.

If a question can be answered by exploring the codebase, explore the codebase instead.

Stop when every open branch is resolved (or I call it), then summarize the resolved decisions and any deferred or still-open items as a short decision log.
