# chatgpt-bridge

Ask the ChatGPT desktop app a question from the command line, and get the answer
back on stdout.

Answers come out of the ChatGPT **conversation** allowance rather than the Codex
quota `codex exec` spends. That distinction is not something a flag can change:
the quota pool follows the endpoint, and a ChatGPT subscription's only
programmatic endpoint is the Codex one. Driving the app's own UI is the way to
reach the other pool.

## Install

```sh
chmod +x chatgpt_ask.py
ln -s "$PWD/chatgpt_ask.py" ~/.local/bin/chatgpt-ask
```

Requires `uv` and ChatGPT.app, signed in. The script declares its own dependency
inline (PEP 723), so nothing else needs installing — the shebang runs it through
`uv run`.

**Link it rather than copying it.** Several projects use this, and a copy per
project is exactly how the other shared pieces in this repo drifted apart.

## Use

```sh
chatgpt-ask --prompt "proofread this paragraph: ..."
chatgpt-ask --prompt-file question.txt --json --timeout 300
```

`--json` prints `{"ok", "answer", "elapsed_s", "error"}`; exit status is 0 only
when an answer was captured.

| flag | effect |
|---|---|
| `--keep-app` | leave the app running afterwards (saves ~15s per call in a run of several) |
| `--keep-chat` | ask in the open thread instead of a new temporary one |
| `--allow-history` | proceed even if temporary chat is unavailable, saving the thread to history |
| `--port` | debugging port to use (default 9222) |

## What it does to your machine

**It runs the app.** If nothing is serving the debugging port it relaunches
ChatGPT.app with one and quits it again afterwards. An app that was *already*
serving the port is left alone and left running — that one is your session, not
this call's. Don't use the app while a call is in flight.

**It asks in a temporary chat**, so questions never enter your account history.
If that control can't be found it refuses rather than filing a real thread;
`--allow-history` overrides. This is deliberately not "delete the thread
afterwards": a call that dies midway would strand one, and deletion depends on
the sidebar's delete-and-confirm UI holding still.

**The debugging port has no authentication.** While it is open, any process on
the machine can read the conversation and post as you. That is why it is opened
per call instead of left on.

## When it breaks

It finds the composer and the reply by DOM shape — `[contenteditable]`,
`[data-user-message-bubble]`, `_MarkdownRoot_*` — and the send/stop/temporary
controls by their `aria-label` in the app's UI language. An app update that
reshapes any of those breaks it. Re-probe the live DOM to find the new shape
rather than guessing; the constants are all at the top of the file.

Two non-obvious things worth keeping, because both cost a debugging session:

- Reply text is rebuilt from block elements, never read via `innerText`.
  `innerText` returns only *rendered* text, so a backgrounded or occluded window
  reads every message as an empty string while the DOM still holds the answer.
- The debugging port is not reliably IPv4. The same binary comes up on
  `127.0.0.1` one run and `[::1]` the next, so both are probed.
