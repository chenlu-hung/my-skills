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
./install.sh
```

This links `chatgpt_ask.py` into `~/.local/bin/chatgpt-ask` and links this
directory in as a skill for every agent CLI on the machine that loads them:

| CLI | skill directory |
|---|---|
| Claude Code | `~/.claude/skills/chatgpt-bridge` |
| Codex | `~/.codex/skills/chatgpt-bridge` |
| opencode | `~/.config/opencode/skills/chatgpt-bridge` |

All three read the same `SKILL.md`, and every install is a symlink back here, so
there is one script and one set of instructions behind all of them. The installer
is idempotent — re-run it after an update. Removing it is `rm` on the symlinks.

opencode can also auto-load `~/.claude/skills` on its own, but only while
`OPENCODE_DISABLE_EXTERNAL_SKILLS` is unset; installing into its own directory
does not depend on that.

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
| `--doctor` | report what the current host allows, and exit without asking anything |

## Where you call it from

Opening the debugging port means quitting and relaunching the app, because the
flag is only read at launch. Two hosts must not do that, so the script reads its
own environment and process ancestry and refuses instead:

- **Inside ChatGPT.app.** It and the Codex desktop app are one bundle
  (`com.openai.codex`), so a Codex session hosted in the app would close the
  window it is running in.
- **Inside a Codex CLI sandbox.** Children inherit the seatbelt profile, so an
  app relaunched from there comes up unable to write its own container. Under
  `workspace-write` the port is unreachable anyway — networking is off — and the
  script reports that rather than hanging until the timeout expires.

The fix is the same either way: open the port once from an ordinary shell with
`chatgpt-ask --keep-app --prompt ping`, and the restricted call reuses it.

`chatgpt-ask --doctor` prints the host, the port state, whether this call may
manage the app, and a verdict.

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
