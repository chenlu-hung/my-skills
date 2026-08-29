---
name: chatgpt-bridge
description: Ask the ChatGPT desktop app a question from the command line and read the answer on stdout, through the `chatgpt-ask` CLI. The answer is billed to the ChatGPT conversation allowance rather than to the Codex quota that `codex exec` spends, so it is the way to consult GPT without touching the API budget. Runs the same way in Claude Code, Codex, and opencode. Use when the user says "問 ChatGPT", "ask ChatGPT", "chatgpt-ask", "問一下 GPT", "用對話額度問", "別燒 Codex 額度", "second opinion from ChatGPT", or when a question is worth a non-Claude opinion and the desktop app is signed in. macOS only.
---

# chatgpt-bridge

`chatgpt-ask` drives the real ChatGPT.app UI over its Chrome DevTools port. Because
the question travels through the app rather than through an API, the answer comes
out of the **conversation** allowance instead of the Codex quota. No flag can move
that: the pool follows the endpoint, and a ChatGPT subscription's only programmatic
endpoint is the Codex one.

## Asking

```sh
chatgpt-ask --prompt "proofread this paragraph: ..."
chatgpt-ask --prompt-file question.txt --json --timeout 300
```

Long or multi-line prompts belong in `--prompt-file`; shell quoting is the usual
way this goes wrong. `--json` prints `{"ok", "answer", "elapsed_s", "error"}`, and
exit status is 0 only when an answer was actually captured — check one or the
other, since a timeout still prints whatever partial text had arrived.

| flag | effect |
|---|---|
| `--keep-app` | leave the app running afterwards, saving ~15s of relaunch on the next call |
| `--keep-chat` | ask in the open thread instead of a new temporary one |
| `--allow-history` | proceed even when temporary chat is unavailable, saving the thread to history |
| `--timeout` | seconds to wait for the answer (default 300) |
| `--port` | debugging port (default 9222) |
| `--doctor` | report what this host allows, and exit |

## What the other side can see

Only the prompt text. There is no filesystem, no repo, no tools on that end — a
question about code has to carry the code inside it. Treat each call as one
self-contained question to a model that has never seen this machine.

A call takes tens of seconds, most of it launch and streaming, so batch related
questions into one prompt rather than issuing several. When several calls really
are needed, pass `--keep-app` to every one of them.

Don't drive the app by hand while a call is in flight; the script is typing into
the same composer.

## Per host

The script detects its host and refuses rather than doing damage, but knowing the
shape in advance saves a round trip.

**Claude Code** — works as installed.

**Codex CLI** — the default `workspace-write` sandbox has networking off, which
puts the debugging port out of reach; `chatgpt-ask` says so instead of hanging.
Either rerun codex with `--sandbox danger-full-access`, or open the port once from
an ordinary terminal (`chatgpt-ask --keep-app --prompt ping`) and let the sandboxed
call reuse it. Even with the network on, a sandboxed call will not relaunch the
app, because a child launched inside seatbelt inherits it and cannot write its own
container.

**A Codex session hosted inside ChatGPT.app** — the desktop app and the ChatGPT app
are one bundle (`com.openai.codex`), so relaunching it would close the window
running the session. The script walks its own process ancestry, notices, and stops.
Open the port from outside and retry.

**opencode** — needs permission to run bash. It ships installed under
`~/.config/opencode/skills/`, not by way of opencode's auto-load of
`~/.claude/skills`, which is off whenever `OPENCODE_DISABLE_EXTERNAL_SKILLS` is set.

## When a call fails

```sh
chatgpt-ask --doctor
```

prints the host, whether the app is installed and running, whether the port is
being served, whether this call may manage the app, and a verdict. Most failures
are one of: no signed-in app, a port nobody opened, or a host that isn't allowed to
open one.

A failure that isn't in that list is usually the app's DOM having moved under an
update — the selectors and `aria-label` sets are all at the top of `chatgpt_ask.py`,
and `README.md` in this directory explains how to re-probe them.
