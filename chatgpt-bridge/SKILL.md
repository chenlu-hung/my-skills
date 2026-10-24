---
name: chatgpt-bridge
description: Ask the ChatGPT desktop app a question from the command line and read the answer on stdout, through the `chatgpt-ask` CLI — by default in a throwaway temporary chat, or, for research collaboration, inside a ChatGPT project or an existing saved thread, plus reading a whole thread's transcript and listing a project's chats and instructions. The answer is billed to the ChatGPT conversation allowance rather than to the Codex quota that `codex exec` spends, so it is the way to consult GPT without touching the API budget. Runs the same way in Claude Code, Codex, and opencode. Use when the user says "問 ChatGPT", "ask ChatGPT", "chatgpt-ask", "問一下 GPT", "用對話額度問", "別燒 Codex 額度", "second opinion from ChatGPT", "看我的 ChatGPT 專案", "加入 ChatGPT 專案", "讀那個 ChatGPT 對話", "接續 ChatGPT 對話", "join my ChatGPT project", or when a question is worth a non-Claude opinion and the desktop app is signed in. macOS only.
---

# chatgpt-bridge

`chatgpt-ask` drives the real ChatGPT.app UI over its Chrome DevTools port. Because
the question travels through the app rather than through an API, the answer comes
out of the **conversation** allowance instead of the Codex quota. No flag can move
that: the pool follows the endpoint, and a ChatGPT subscription's only programmatic
endpoint is the Codex one.

Everything goes in as the signed-in user, so on the other side it is the user's own
account: custom instructions, memory, plugins and skills apply exactly as if they
had typed it.

## Asking — the default

```sh
chatgpt-ask --prompt "proofread this paragraph: ..."
chatgpt-ask --prompt-file question.txt --json --timeout 300
```

Each call opens a **temporary chat**: nothing reaches the user's history, and the
next call starts from nothing. That is the right mode for almost every question.

Long or multi-line prompts belong in `--prompt-file`; shell quoting is the usual
way this goes wrong. `--json` prints
`{"ok", "answer", "elapsed_s", "error", "chat_url", "conversation_id", "project_id", "temporary", "skills"}`,
and exit status is 0 only when an answer was actually captured — check one or the
other, since a timeout still prints whatever partial text had arrived. `skills`
names the ChatGPT skills the turn used, which is how to tell whether a skill the
user expects actually fired.

## Research collaboration — projects and saved threads

The user's research runs in ChatGPT first — brainstorming and literature review —
and only once a direction is settled do they open a ChatGPT project and bring you
in to check the direction and implement it. These modes serve that phase, and
every one of them is explicit: never switch to them on your own because a repo
looks like research. A casual question stays temporary.

```sh
chatgpt-ask --list-project                          # the sidebar's projects
chatgpt-ask --list-project "Spherical"              # one project: chats, files, instructions
chatgpt-ask --read <chat-id|url> > thread.md        # a whole thread, as markdown; sends nothing
chatgpt-ask --project "Spherical" --prompt-file q.txt --json   # new chat inside the project
chatgpt-ask --chat <chat-id|url> --prompt-file q.txt --json    # continue a saved thread
```

Projects match by id, URL, or (partial, case-insensitive) name; an ambiguous or
missing match fails and lists the candidates rather than guessing.

How to join a project:

1. **Read before you speak.** `--list-project` gives the project's instructions and
   its chats; `--read` each thread that holds the direction. Redirect to a file and
   read it in parts — research threads run to 100–200 KB. Read the transcripts
   themselves rather than asking ChatGPT to summarize them: you are there for an
   independent check, and its summary of its own work carries its framing with it.
2. **Open your own thread** with `--project`, rather than appending to the user's
   brainstorming threads. A new project chat still gets the project's instructions,
   files and (depending on the project's memory setting) its other chats.
3. **Continue it** with `--chat` and the `chat_url` the first call returned.
4. **Record where it lives.** Write `.chatgpt-bridge.json` at the root of the
   research repo so a later session finds the thread without asking:

   ```json
   {"project": "Spherical Neural Operators for Weather Forecasting",
    "project_id": "g-p-…",
    "review_chat": "https://chatgpt.com/g/g-p-…/c/…"}
   ```

   Check for that file before opening a new thread in a project.

Anything sent into a saved thread is prefixed with `【Claude Code】` (`--tag`
changes it; `--tag ''` drops it), because in the app it shows up as the user's own
message. Saved-thread modes wait up to 900 s by default instead of 300; research
answers, and especially deep research, can take longer — raise `--timeout`.

What `--read` returns per turn: the user's message, ChatGPT's answer as markdown
(math as `\(...\)`, tables, citations as links — answers rendered as generated UI
get a source list instead of inline citations), the skills used, and the files
involved. Files ChatGPT *generated* (reports, JSON) are named but not downloaded —
if one matters, ask the user to download it into the repo. Project files are not
readable at all except by asking ChatGPT about them. `--list-project` lists the
chats the sidebar shows for the project.

## Flags

| flag | effect |
|---|---|
| `--project NAME\|ID\|URL` | ask in a new chat inside that project |
| `--chat ID\|URL` | ask in that saved conversation |
| `--keep-chat` | ask in whatever thread is open (temporary or not) instead of a new temporary one |
| `--read ID\|URL` | print the thread's transcript; sends nothing |
| `--list-project [NAME]` | list projects, or one project's chats, files and instructions; sends nothing |
| `--tag TEXT` | prefix for questions that land in a saved thread (default `【Claude Code】`) |
| `--allow-history` | proceed even when temporary chat is unavailable, saving the thread to history |
| `--keep-app` | leave the app running afterwards, saving ~15s of relaunch on the next call |
| `--timeout` | seconds to wait for the answer (default 300; 900 for saved threads) |
| `--port` | debugging port (default 9222) |
| `--doctor` | report what this host allows, and exit |

## What the other side can see

In a temporary chat, only the prompt text. In a project, also the project's
instructions, files and memory; in a saved thread, the thread so far. There is
never a filesystem, a repo, or tools reaching this machine — a question about code
has to carry the code inside it.

A call takes seconds to minutes, most of it launch and generation, so batch related
questions into one prompt rather than issuing several. When several calls really
are needed, pass `--keep-app` to every one of them.

Don't drive the app by hand while a call is in flight; the script types into the
same composer, and every mode switches the app's window to the thread it works on.

## Per host

The script detects its host and refuses rather than doing damage, but knowing the
shape in advance saves a round trip.

**Claude Code** — works as installed.

**Codex CLI** — the default `workspace-write` sandbox has networking off, which
puts the debugging port out of reach; `chatgpt-ask` says so instead of hanging.
Either rerun codex with `--sandbox danger-full-access`, or open the port once from
an ordinary terminal (`chatgpt-ask --keep-app --list-project`) and let the sandboxed
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

A failure that isn't in that list is usually the app having moved under an update.
The script reads the app through React props and a few `data-*` attributes, all in
the `PRELUDE` at the top of `chatgpt_ask.py`; `README.md` in this directory explains
how to re-probe them.
