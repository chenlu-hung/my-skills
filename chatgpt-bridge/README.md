# chatgpt-bridge

Ask the ChatGPT desktop app a question from the command line, and get the answer
back on stdout — in a throwaway temporary chat, or inside a ChatGPT project or a
saved thread when the work is meant to persist. It can also read a whole thread
back as markdown and list a project's chats and instructions.

Answers come out of the ChatGPT **conversation** allowance rather than the Codex
quota `codex exec` spends. That distinction is not something a flag can change:
the quota pool follows the endpoint, and a ChatGPT subscription's only
programmatic endpoint is the Codex one. Driving the app's own UI is the way to
reach the other pool.

## Install

```sh
./install.sh
```

This puts `chatgpt-ask` on `~/.local/bin` and links this directory in as a skill
for every agent CLI on the machine that loads them:

| CLI | skill directory |
|---|---|
| Claude Code | `~/.claude/skills/chatgpt-bridge` |
| Codex | `~/.codex/skills/chatgpt-bridge` |
| opencode | `~/.config/opencode/skills/chatgpt-bridge` |

All three read the same `SKILL.md`, and every install points back here, so there
is one script and one set of instructions behind all of them. The installer is
idempotent — re-run it after an update. Removing it is `rm` on the links.

The script needs a Python with `websockets`. Where conda is installed the
installer creates (or completes) a `chatgpt-bridge` env and makes `chatgpt-ask` a
two-line launcher that runs `chatgpt_ask.py` from this directory with that env's
Python. Without conda, `chatgpt-ask` is a symlink and the script's PEP 723 shebang
has `uv` resolve the dependency. `CHATGPT_BRIDGE_RUNNER=conda|uv` overrides the
choice. Either way it needs ChatGPT.app, signed in.

opencode can also auto-load `~/.claude/skills` on its own, but only while
`OPENCODE_DISABLE_EXTERNAL_SKILLS` is unset; installing into its own directory
does not depend on that.

**Link it rather than copying it.** Several projects use this, and a copy per
project is exactly how the other shared pieces in this repo drifted apart.

## Use

```sh
chatgpt-ask --prompt "proofread this paragraph: ..."            # temporary chat
chatgpt-ask --prompt-file question.txt --json --timeout 300

chatgpt-ask --list-project                                      # projects in the sidebar
chatgpt-ask --list-project "Spherical"                          # chats, files, instructions
chatgpt-ask --read https://chatgpt.com/g/g-p-…/c/… > thread.md   # transcript; sends nothing
chatgpt-ask --project "Spherical" --prompt-file q.txt --json    # new chat in the project
chatgpt-ask --chat 6adb8707-… --prompt-file q.txt --json        # continue a saved thread
```

`--json` prints `{"ok", "answer", "elapsed_s", "error", "chat_url",
"conversation_id", "project_id", "temporary", "skills"}`; exit status is 0 only
when an answer was captured. Without `--json`, a saved thread's URL goes to
stderr as `chat: …`.

| flag | effect |
|---|---|
| `--project NAME\|ID\|URL` | ask in a new chat inside that project |
| `--chat ID\|URL` | ask in that saved conversation |
| `--keep-chat` | ask in the open thread instead of a new temporary one |
| `--read ID\|URL` | print a thread as markdown, sending nothing |
| `--list-project [NAME]` | list projects, or one project's chats, files and instructions |
| `--tag TEXT` | prefix for questions sent into a saved thread (default `【Claude Code】`, `''` for none) |
| `--allow-history` | proceed even if temporary chat is unavailable, saving the thread to history |
| `--keep-app` | leave the app running afterwards (saves ~15s per call in a run of several) |
| `--timeout` | seconds to wait (default 300; 900 with `--project`/`--chat`/`--keep-chat`) |
| `--port` | debugging port to use (default 9222) |
| `--doctor` | report what the current host allows, and exit without touching the app |

`--read` turns each turn into markdown: math stays LaTeX, tables stay tables, and
citations become links. Answers the app renders as generated UI keep their
structure but lose inline citations, so they get a source list appended instead.
Deep-research reports come out whole. Files ChatGPT generated are named but not
downloaded.

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
`chatgpt-ask --keep-app --list-project`, which asks nothing, and the restricted
call reuses it.

`chatgpt-ask --doctor` prints the host, the port state, whether this call may
manage the app, and a verdict.

## What it does to your machine

**It runs the app.** If nothing is serving the debugging port it relaunches
ChatGPT.app with one and quits it again afterwards. An app that was *already*
serving the port is left alone and left running — that one is your session, not
this call's. Every mode switches the window to the thread it works on, so don't
use the app while a call is in flight.

**By default it asks in a temporary chat**, so questions never enter your account
history. Whether that worked is read from the composer's own state, not inferred
from a button label; if the mode can't be switched on it refuses rather than
filing a real thread (`--allow-history` overrides). This is deliberately not
"delete the thread afterwards": a call that dies midway would strand one, and
deletion depends on the sidebar's delete-and-confirm UI holding still.

**`--project`, `--chat` and `--keep-chat` write into your account on purpose.**
Those threads are saved, may feed memory, and — in a shared project — are visible
to its members. What the bridge types appears as your own message, which is why it
is prefixed with `--tag`.

**The debugging port has no authentication.** While it is open, any process on
the machine can read the conversation and post as you. That is why it is opened
per call instead of left on.

## When it breaks

It finds its way around the app through a handful of `data-*` attributes
(`form[data-chatgpt-composer]`, `[data-app-action-timeline-scroll]`, the sidebar's
`data-app-action-sidebar-project-*`) and, for everything that matters, through
React props reached from those elements: the router, the composer's thread state
(`conversationId`, `projectId`, `isTemporaryChat`), and the turn list's `entries`.
All of it lives in `PRELUDE` at the top of `chatgpt_ask.py`. An app update that
reshapes any of it breaks the bridge. Re-probe the live app rather than guessing:
start it with `--remote-debugging-port=9222`, connect to the `app://-/index.html`
page target, and walk `__reactFiber$…` from those elements.

Things that each cost a wrong answer once, worth keeping:

- **Every thread you open stays mounted, hidden.** Anything queried over the
  whole document mixes turns from several threads. Only the *visible* timeline
  and composer belong to the thread on screen.
- **The timeline is virtualized.** Only turns near the viewport are in the DOM,
  so the DOM is a window onto the thread, not the thread; the turn list's
  `entries` prop has all of it.
- **The temporary-chat label changed under us.** "開啟暫存對話" became "暫存對話",
  and a check keyed on the verb read that as "already on" and asked in a saved
  chat. State comes from props now; the label only locates the button.
- **The hidden avatar-overlay window has a composer too**, and it is the first
  page target listed. Windows are chosen by URL, not by "has a composer".
- **Citations come in two encodings.** Raw private-use tokens
  (`U+E200 … U+E201`, as in deep-research reports) and the
  `:chatgpt-content-reference{index=N}` directives the app has already turned an
  ordinary answer's tokens into. Both resolve through the message's content
  references, whose `alt` is the markdown the app's own copy button uses.
- **Rendered text is not the answer.** `innerText` is empty for an occluded
  window, and `textContent` flattens tables and duplicates KaTeX math. Answers
  are rebuilt from the message source, never from rendering.
- The debugging port is not reliably IPv4. The same binary comes up on
  `127.0.0.1` one run and `[::1]` the next, so both are probed.
