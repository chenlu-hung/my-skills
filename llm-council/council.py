#!/usr/bin/env python3
"""council.py — dispatch one prompt to the external LLM-council members in parallel.

Part of the `llm-council` Claude Code skill. The orchestrating Claude Code session is
the Chairman and does not answer; this script drives every member CLI — including a
separate headless `claude` — so they run concurrently with clean, parsed output.

Each member authenticates through its own *subscription / sign-in*, not an API key:
  - codex     -> OpenAI Codex CLI, signed in with a ChatGPT subscription (`codex exec`)
  - gemini    -> Google Antigravity CLI `agy`, Gemini models (`agy -p`)
  - claude    -> Claude Code headless (`claude -p`) — Claude as an independent member,
                 separate from the orchestrating session that chairs the council

Usage:
    python3 council.py --prompt-file q.txt                 # all members
    python3 council.py --members codex,claude --prompt "..."   # a subset
    echo "question" | python3 council.py                   # prompt via stdin
    python3 council.py --prompt-file review.txt --workdir /path/to/repo
        # members run *in* that repo so they can read real files (review work);
        # they are hardened read-only and the directory is never deleted
    python3 council.py --anonymize stage1.json --question-file q.txt
        # no dispatch: shuffle + relabel the stage-1 answers deterministically,
        # write review_prompt.txt (self-contained cross-review prompt) and
        # label_map.json (private label→member mapping) next to stage1.json

Output: JSON on stdout:
    {"members": {"codex": {ok, answer, model, elapsed_s, error}, "gemini": {...}}}

By default every CLI runs in a throwaway temp dir, so it cannot see or touch the
user's repo while answering. `--workdir` deliberately gives that up: members run in a
directory the caller owns (review work, where the answer depends on real code), and
the script instead hardens each one read-only. Those guarantees are NOT equal:

  codex   OS-level sandbox (`-s read-only`)          — enforced
  claude  write tools denied, no settings loaded     — enforced by the harness
  gemini  `--mode plan`                              — an agent mode, not a sandbox

Gemini's is the weakest link: `--mode plan` is a behavioural mode and it is paired with
`--dangerously-skip-permissions` (without which agy cannot read anything headlessly). It
held under test — told explicitly to create and overwrite files in a borrowed dir, it did
neither — but it is a promise, not a wall. Review from a clean/committed tree so anything
unexpected is recoverable.
"""

import argparse
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TIMEOUT = 300  # seconds, per member — matches agy's default --print-timeout
DEFAULT_GEMINI_MODEL = "Gemini 3.1 Pro (High)"
DEFAULT_CODEX_MODEL = ""  # empty = whatever the ChatGPT subscription defaults to
DEFAULT_CLAUDE_MODEL = ""  # empty = whatever the Claude subscription defaults to
ALL_MEMBERS = "codex,gemini,claude"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # strip terminal color codes from CLI stdout


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(HERE, "schema", "answer.schema.json")


def load_schema(path):
    """Read a JSON Schema file -> (path, compact_json_string). Both forms are needed:
    codex and agy take a FILE, claude takes an inline JSON STRING."""
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    return path, json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def render_structured(obj):
    """Turn a schema-shaped answer back into Markdown.

    Structured output is only useful if it does not cost us the prose: Stage-3 synthesis and
    `--anonymize` read `answer` as text, and so does the human. So every member's JSON is
    rendered here and the raw object is kept alongside it under `structured`.
    """
    if not isinstance(obj, dict):
        return ""
    parts = [(obj.get("answer") or obj.get("summary") or "").strip()]
    for key, heading in (("key_points", "重點"), ("caveats", "限制"),
                         ("files_changed", "改動的檔案"), ("tests_run", "跑過的測試"),
                         ("blockers", "卡住的地方"), ("followups", "留給後續")):
        items = obj.get(key)
        if isinstance(items, list) and items:
            parts.append(f"**{heading}**\n" + "\n".join(f"- {i}" for i in items))
    conf = obj.get("confidence")
    if conf:
        parts.append(f"_confidence: {conf}_")
    return "\n\n".join(x for x in parts if x).strip()


def parse_structured(text):
    """(rendered_markdown, raw_obj) from a member's stdout/-o payload.

    Falls back to (text, None) when the payload is not schema-shaped JSON — a member that
    ignored the schema must still contribute its prose rather than being dropped.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return stripped, None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped, None
    rendered = render_structured(obj)
    return (rendered or stripped), (obj if rendered else None)

def codex_rollout_fallback(started_at):
    """Last-resort recovery of a codex answer whose stdout/`-o` capture came back empty.

    `codex exec` always persists the full turn to ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl,
    so a lost final message is recoverable. Returns "" when nothing usable is found.
    """
    root = os.path.expanduser("~/.codex/sessions")
    if not os.path.isdir(root):
        return ""
    newest, newest_mtime = None, started_at - 5
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = path, mtime
    if not newest:
        return ""
    texts = []
    try:
        with open(newest, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = rec.get("payload", rec)
                if payload.get("role") != "assistant":
                    continue
                for chunk in payload.get("content") or []:
                    if chunk.get("type") == "output_text" and chunk.get("text"):
                        texts.append(chunk["text"])
    except OSError:
        return ""
    return texts[-1].strip() if texts else ""


def run_codex(prompt, model, timeout, workdir, borrowed, schema=None):
    """Codex CLI (ChatGPT subscription). `-o` writes only the final message — clean capture.

    The `-o` file goes to its own temp dir, never into `workdir`: when the caller borrows a
    real repo (`--workdir`), dropping `codex_answer.txt` in it would dirty their tree.

    `stdin=DEVNULL` is load-bearing: when stdin is a pipe rather than a TTY (which is the
    case for every call from an agent harness), `codex exec` prints
    "Reading additional input from stdin..." and tries to read a SECOND prompt from it.
    The turn then gets truncated — stdout holds one status line and `-o` is never written,
    while `codex doctor` reports everything healthy. Verified: same prompt, 39 bytes of
    stdout and no `-o` file without DEVNULL; 622 bytes and a correct `-o` file with it.
    """
    outdir = tempfile.mkdtemp(prefix="council-codex-")
    outfile = os.path.join(outdir, "codex_answer.txt")
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",   # a temp workdir is not a git repo
        "-s", "read-only",         # cannot write/execute against the filesystem
        "--cd", workdir,
        "-o", outfile,
    ]
    if model:
        cmd += ["-m", model]
    if schema:
        cmd += ["--output-schema", schema[0]]   # codex wants a FILE
    cmd.append(prompt)

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        elapsed = round(time.time() - t0, 1)
        answer = ""
        if os.path.exists(outfile):
            with open(outfile, encoding="utf-8") as fh:
                answer = fh.read().strip()
        if not answer:
            answer = codex_rollout_fallback(t0)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)

    answer, structured = parse_structured(answer)
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "structured": structured,
            "model": model or "default", "elapsed_s": elapsed, "error": err}


def run_gemini(prompt, model, timeout, workdir, borrowed, schema=None):
    """Antigravity CLI `agy` in print mode. `--model` must precede `-p` (Go flag parsing).

    On a borrowed workdir the pair of flags is deliberate and must stay together:
      --mode plan                     the actual write protection — verified by test: agy
                                      read the directory but created no file and clobbered
                                      nothing when explicitly told to
      --dangerously-skip-permissions  without it agy cannot read *anything* here — its read
                                      tools need the "command" permission, and headless mode
                                      has no way to prompt, so every call auto-denies and
                                      returns empty. It only suppresses the prompt; plan mode
                                      still forbids the writes.
    """
    cmd = ["agy"]
    if model:
        cmd += ["--model", model]
    # agy's own print-mode deadline defaults to 5m and is INDEPENDENT of our subprocess
    # timeout: without this, a --timeout 900 review still has agy give up at 300s.
    cmd += ["--print-timeout", f"{timeout}s"]
    if schema:
        # agy rejects --json-schema unless the output format is json; the envelope then
        # carries the parsed object under "structured_output".
        cmd += ["--output-format", "json", "--json-schema", schema[0]]
    if borrowed:
        cmd += ["--mode", "plan", "--dangerously-skip-permissions"]
    cmd += ["-p", prompt]

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir,
                          stdin=subprocess.DEVNULL)
    elapsed = round(time.time() - t0, 1)

    raw = ANSI.sub("", proc.stdout or "").strip()
    structured = None
    if schema and raw.startswith("{"):
        try:  # unwrap agy's envelope before parsing the payload
            env = json.loads(raw)
            inner = env.get("structured_output")
            if inner is not None:
                raw = json.dumps(inner, ensure_ascii=False)
            elif isinstance(env.get("response"), str):
                raw = env["response"]
        except json.JSONDecodeError:
            pass
    answer, structured = parse_structured(raw)
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "structured": structured,
            "model": model or "default", "elapsed_s": elapsed, "error": err}


def run_claude(prompt, model, timeout, workdir, borrowed, schema=None):
    """Claude Code headless (`claude -p`); stdout is the clean answer.

    `--setting-sources project` keeps OAuth/keychain auth but skips *user* settings, so the
    member doesn't fire the user's SessionStart hooks (e.g. the handoff notice) into its
    answer. In the default temp `workdir` no project/local settings load either.
    (`--bare` would also drop hooks but it skips keychain reads too, which breaks sub auth.)

    On a borrowed workdir two things change:

    1. `--setting-sources ""` — load NO settings at all. `project` was safe only while the cwd
       was an empty temp dir; pointed at a real repo it loads that repo's `.claude/settings.json`
       and *runs its hooks*. Verified: a repo with a SessionStart hook echoing a marker had that
       marker land in the member's answer. Hooks are arbitrary shell commands, so this is an
       execution path, not just output noise. The empty value still keeps OAuth/keychain auth.
    2. The write tools are denied, so the member can read the caller's repo but not touch it.
       Bash is denied too — it is a write path (`>`, `rm`) the other denials miss; Read/Grep/Glob
       are enough to review with. Keep `--disallowedTools` LAST: it is variadic and will swallow
       whatever follows it.

    The prompt goes in on **stdin**, not as an argument, for that same variadic reason: a
    trailing positional prompt gets eaten as more tool names ("Permission deny rule \"the\"
    matches no known tool"), then the call dies for having no prompt.
    """
    cmd = ["claude", "-p"]
    cmd += ["--setting-sources", "" if borrowed else "project"]
    if model:
        cmd += ["--model", model]
    if schema:
        cmd += ["--json-schema", schema[1]]   # claude wants an inline JSON STRING, not a path
    if borrowed:
        cmd += ["--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash"]

    t0 = time.time()
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=timeout, cwd=workdir)
    elapsed = round(time.time() - t0, 1)

    answer, structured = parse_structured(proc.stdout or "")
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "structured": structured,
            "model": model or "default", "elapsed_s": elapsed, "error": err}


CHATGPT_ASK = os.path.join(HERE, "chatgpt_ask.py")


def run_chatgpt(prompt, model, timeout, workdir, borrowed, schema=None):
    """ChatGPT desktop app, driven over its debugging port by `chatgpt_ask.py`.

    This member answers out of the ChatGPT **conversation** allowance instead of the Codex
    quota `run_codex` spends, which is the whole reason it exists. The app must already be
    running with `--remote-debugging-port`; see chatgpt_ask.py for the launch line.

    Two things it cannot do that the CLI members can:

    1. Read a borrowed `--workdir`. It answers from inside the app and has no filesystem, so
       it refuses rather than answering from the prompt alone while the caller believes it
       reviewed their repo.
    2. Enforce a schema. There is no `--output-schema` equivalent in the UI, so the schema is
       appended to the prompt as a request. `parse_structured` already degrades to prose when
       a member ignores it, so a non-JSON reply still counts as an answer.
    """
    if borrowed:
        return {"ok": False, "answer": "", "structured": None, "model": "desktop app",
                "elapsed_s": 0,
                "error": "chatgpt member cannot read a borrowed --workdir (it has no filesystem)"}

    if schema:
        prompt = f"{prompt}\n\nRespond with ONLY a JSON object matching this schema:\n{schema[1]}"

    promptdir = tempfile.mkdtemp(prefix="council-chatgpt-")
    promptfile = os.path.join(promptdir, "question.txt")
    try:
        with open(promptfile, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        cmd = [sys.executable, CHATGPT_ASK, "--prompt-file", promptfile,
               "--json", "--timeout", str(timeout)]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30,
                              stdin=subprocess.DEVNULL)
        elapsed = round(time.time() - t0, 1)
    finally:
        shutil.rmtree(promptdir, ignore_errors=True)

    try:
        result = json.loads(proc.stdout)
    except (TypeError, ValueError):
        err = (proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, unreadable output"
        return {"ok": False, "answer": "", "structured": None, "model": "desktop app",
                "elapsed_s": elapsed, "error": err}

    answer, structured = parse_structured(result.get("answer", ""))
    return {"ok": bool(result.get("ok")) and bool(answer), "answer": answer,
            "structured": structured, "model": model or "desktop app",
            "elapsed_s": result.get("elapsed_s", elapsed), "error": result.get("error", "")}


RUNNERS = {"codex": run_codex, "gemini": run_gemini, "claude": run_claude,
           "chatgpt": run_chatgpt}

# CLI binary each member shells out to — used for the "not installed" error message.
CLI_BIN = {"codex": "codex", "gemini": "agy", "claude": "claude", "chatgpt": sys.executable}


def dispatch(name, prompt, model, timeout, workdir, borrowed, schema=None):
    """Wrap a runner so a missing CLI / timeout / crash becomes a structured error, never an exception."""
    try:
        return RUNNERS[name](prompt, model, timeout, workdir, borrowed, schema=schema)
    except subprocess.TimeoutExpired:
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": timeout,
                "error": f"timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": 0,
                "error": f"`{CLI_BIN[name]}` not found on PATH — is the CLI installed and signed in?"}
    except Exception as exc:  # noqa: BLE001 — surface anything else as a member error
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def anonymize(stage1_path, question_file):
    """Turn a saved stage-1 JSON into a shuffled, relabelled cross-review prompt.

    Doing the shuffle + labelling here (not in the orchestrating LLM) means the
    label→member mapping never has to enter the chair's context before synthesis,
    so it cannot leak into a reviewer prompt.
    """
    with open(stage1_path, encoding="utf-8") as fh:
        data = json.load(fh)
    with open(question_file, encoding="utf-8") as fh:
        question = fh.read().strip()

    members = data.get("members", {})
    answered = [(m, (r.get("answer") or "").strip()) for m, r in members.items()
                if r.get("ok") and (r.get("answer") or "").strip()]
    dropouts = sorted(set(members) - {m for m, _ in answered})
    if len(answered) < 2:
        sys.exit("council.py: fewer than 2 usable answers — nothing to cross-review; "
                 "skip Stage 2 and synthesize directly from the answers you have")
    if len(answered) > len(string.ascii_uppercase):
        sys.exit("council.py: too many answers to label A–Z")

    random.shuffle(answered)
    labels = string.ascii_uppercase[: len(answered)]
    mapping = {labels[i]: m for i, (m, _) in enumerate(answered)}

    blocks = "\n\n".join(f"--- Response {labels[i]} ---\n{a}" for i, (_, a) in enumerate(answered))
    prompt = (
        f"Question: {question}\n\n"
        "Below are anonymous responses to this question. Evaluate each for correctness,\n"
        "depth, and usefulness, then rank them best-to-worst with a one-line justification\n"
        "each. If a response contains a specific factual or correctness error, quote the\n"
        "erroneous claim and say why it is wrong.\n\n"
        f"{blocks}\n"
    )

    outdir = os.path.dirname(os.path.abspath(stage1_path))
    prompt_path = os.path.join(outdir, "review_prompt.txt")
    map_path = os.path.join(outdir, "label_map.json")
    with open(prompt_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)
    with open(map_path, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=2)

    json.dump({"review_prompt": prompt_path, "label_map": map_path,
               "responses": len(answered), "dropouts": dropouts},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def read_prompt(args):
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            return fh.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    sys.exit("council.py: no prompt given (use --prompt, --prompt-file, or stdin)")


def main():
    ap = argparse.ArgumentParser(description="Dispatch a prompt to external LLM-council members in parallel.")
    ap.add_argument("--members", default=ALL_MEMBERS,
                    help=f"comma-separated subset of: {', '.join(RUNNERS)} "
                         f"(default: {ALL_MEMBERS}). `chatgpt` is opt-in: it needs the desktop "
                         "app already running on a debugging port, and it cannot read --workdir")
    ap.add_argument("--prompt", help="prompt text (else --prompt-file, else stdin)")
    ap.add_argument("--prompt-file", help="file containing the prompt")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="per-member timeout in seconds")
    ap.add_argument("--workdir", help="directory the members run in (default: a throwaway temp dir). "
                    "Point it at a repo so members can READ real files — needed for review work, "
                    "where the answer depends on code the prompt cannot fully carry. Members are "
                    "hardened read-only in this mode and the directory is never deleted.")
    ap.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL, help="Antigravity model name")
    ap.add_argument("--codex-model", default=DEFAULT_CODEX_MODEL, help="Codex model (empty = subscription default)")
    ap.add_argument("--output-schema", metavar="FILE", default=DEFAULT_SCHEMA,
                    help="JSON Schema every member's final answer must match "
                         f"(default: {os.path.relpath(DEFAULT_SCHEMA, HERE)}). All three CLIs "
                         "support it — codex `--output-schema`, agy `--json-schema` (needs "
                         "`--output-format json`), claude `--json-schema` — so members stay "
                         "comparable. The JSON is rendered back to Markdown into `answer`, "
                         "with the raw object kept under `structured`, so synthesis and "
                         "--anonymize are unaffected.")
    ap.add_argument("--no-output-schema", action="store_true",
                    help="disable structured output; every member returns free prose")
    ap.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL, help="Claude model (empty = subscription default)")
    ap.add_argument("--anonymize", metavar="STAGE1_JSON",
                    help="don't dispatch; build an anonymized cross-review prompt from a saved stage-1 JSON")
    ap.add_argument("--question-file", help="original question file (required with --anonymize)")
    args = ap.parse_args()

    if args.anonymize:
        if not args.question_file:
            sys.exit("council.py: --anonymize requires --question-file")
        anonymize(args.anonymize, args.question_file)
        return

    members = [m.strip() for m in args.members.split(",") if m.strip()]
    unknown = [m for m in members if m not in RUNNERS]
    if unknown:
        sys.exit(f"council.py: unknown member(s): {', '.join(unknown)} (valid: {', '.join(RUNNERS)})")

    prompt = read_prompt(args)

    schema = None
    if not args.no_output_schema:
        try:
            schema = load_schema(args.output_schema)
        except (OSError, json.JSONDecodeError) as exc:
            sys.exit(f"council.py: --output-schema unreadable ({args.output_schema}): {exc}")

    models = {
        "codex": args.codex_model,
        "gemini": args.gemini_model,
        "claude": args.claude_model,
        # the chatgpt member uses whatever model is selected in the app's own UI
        "chatgpt": "",
    }

    # `borrowed` = the caller handed us a directory we do not own. It gates two things:
    # the read-only hardening above, and (critically) whether we may delete the dir.
    borrowed = bool(args.workdir)
    if borrowed:
        workdir = os.path.abspath(os.path.expanduser(args.workdir))
        if not os.path.isdir(workdir):
            sys.exit(f"council.py: --workdir is not a directory: {workdir}")
    else:
        workdir = tempfile.mkdtemp(prefix="llm-council-")

    try:
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            futures = {m: pool.submit(dispatch, m, prompt, models[m], args.timeout, workdir,
                                      borrowed, schema)
                       for m in members}
            results = {m: f.result() for m, f in futures.items()}
    finally:
        if not borrowed:  # never rmtree a directory the caller owns
            shutil.rmtree(workdir, ignore_errors=True)

    json.dump({"members": results}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
