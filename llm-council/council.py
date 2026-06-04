#!/usr/bin/env python3
"""council.py — dispatch one prompt to the external LLM-council members in parallel.

Part of the `llm-council` Claude Code skill. Claude Code itself is the third member
**and** the Chairman, so it answers directly; this script only drives the external
CLIs (Codex, Antigravity) so they run concurrently with clean, parsed output.

Each member authenticates through its own *subscription / sign-in*, not an API key:
  - codex     -> OpenAI Codex CLI, signed in with a ChatGPT subscription (`codex exec`)
  - gemini    -> Google Antigravity CLI `agy`, Gemini models (`agy -p`)
  - claude    -> Claude Code headless (`claude -p`) — Claude as an independent member,
                 separate from the orchestrating session that chairs the council
  - opencode  -> opencode CLI (`opencode run`), default model DeepSeek V4 Flash (free)

Usage:
    python3 council.py --prompt-file q.txt                 # all members
    python3 council.py --members codex,claude --prompt "..."   # a subset
    echo "question" | python3 council.py                   # prompt via stdin

Output: JSON on stdout:
    {"members": {"codex": {ok, answer, model, elapsed_s, error}, "gemini": {...}}}

The external CLIs run in a throwaway temp dir under a read-only sandbox so they
cannot touch the user's repo while answering.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TIMEOUT = 300  # seconds, per member — matches agy's default --print-timeout
DEFAULT_GEMINI_MODEL = "Gemini 3.1 Pro (High)"
DEFAULT_CODEX_MODEL = ""  # empty = whatever the ChatGPT subscription defaults to
DEFAULT_CLAUDE_MODEL = ""  # empty = whatever the Claude subscription defaults to
DEFAULT_OPENCODE_MODEL = "opencode/deepseek-v4-flash-free"  # free DeepSeek V4 Flash
ALL_MEMBERS = "codex,gemini,claude,opencode"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # strip terminal color codes from CLI stdout


def run_codex(prompt, model, timeout, workdir):
    """Codex CLI (ChatGPT subscription). `-o` writes only the final message — clean capture."""
    outfile = os.path.join(workdir, "codex_answer.txt")
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",   # temp workdir is not a git repo
        "-s", "read-only",         # cannot write/execute against the filesystem
        "--cd", workdir,
        "-o", outfile,
    ]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    elapsed = round(time.time() - t0, 1)

    answer = ""
    if os.path.exists(outfile):
        with open(outfile, encoding="utf-8") as fh:
            answer = fh.read().strip()
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "model": model or "default", "elapsed_s": elapsed, "error": err}


def run_gemini(prompt, model, timeout, workdir):
    """Antigravity CLI `agy` in print mode. `--model` must precede `-p` (Go flag parsing)."""
    cmd = ["agy"]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", prompt]

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    elapsed = round(time.time() - t0, 1)

    answer = ANSI.sub("", proc.stdout or "").strip()
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "model": model or "default", "elapsed_s": elapsed, "error": err}


def run_claude(prompt, model, timeout, workdir):
    """Claude Code headless (`claude -p`); stdout is the clean answer.

    `--setting-sources project` keeps OAuth/keychain auth but skips *user* settings, so the
    member doesn't fire the user's SessionStart hooks (e.g. the handoff notice) into its
    answer. Run in the empty temp `workdir`, so no project/local settings load either.
    (`--bare` would also drop hooks but it skips keychain reads too, which breaks sub auth.)
    """
    cmd = ["claude", "-p", "--setting-sources", "project"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    elapsed = round(time.time() - t0, 1)

    answer = (proc.stdout or "").strip()
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "model": model or "default", "elapsed_s": elapsed, "error": err}


def run_opencode(prompt, model, timeout, workdir):
    """opencode `run` in JSON mode; the answer is the concatenation of `type:text` events."""
    cmd = ["opencode", "run", "--format", "json"]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    elapsed = round(time.time() - t0, 1)

    texts = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "text":
            chunk = (obj.get("part") or {}).get("text")
            if chunk:
                texts.append(chunk)
    answer = "\n".join(texts).strip()
    ok = bool(answer) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty answer")
    return {"ok": ok, "answer": answer, "model": model or DEFAULT_OPENCODE_MODEL, "elapsed_s": elapsed, "error": err}


RUNNERS = {"codex": run_codex, "gemini": run_gemini, "claude": run_claude, "opencode": run_opencode}

# CLI binary each member shells out to — used for the "not installed" error message.
CLI_BIN = {"codex": "codex", "gemini": "agy", "claude": "claude", "opencode": "opencode"}


def dispatch(name, prompt, model, timeout, workdir):
    """Wrap a runner so a missing CLI / timeout / crash becomes a structured error, never an exception."""
    try:
        return RUNNERS[name](prompt, model, timeout, workdir)
    except subprocess.TimeoutExpired:
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": timeout,
                "error": f"timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": 0,
                "error": f"`{CLI_BIN[name]}` not found on PATH — is the CLI installed and signed in?"}
    except Exception as exc:  # noqa: BLE001 — surface anything else as a member error
        return {"ok": False, "answer": "", "model": model or "default", "elapsed_s": 0,
                "error": f"{type(exc).__name__}: {exc}"}


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
                    help=f"comma-separated subset of: {ALL_MEMBERS} (default: all)")
    ap.add_argument("--prompt", help="prompt text (else --prompt-file, else stdin)")
    ap.add_argument("--prompt-file", help="file containing the prompt")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="per-member timeout in seconds")
    ap.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL, help="Antigravity model name")
    ap.add_argument("--codex-model", default=DEFAULT_CODEX_MODEL, help="Codex model (empty = subscription default)")
    ap.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL, help="Claude model (empty = subscription default)")
    ap.add_argument("--opencode-model", default=DEFAULT_OPENCODE_MODEL, help="opencode model as provider/model")
    args = ap.parse_args()

    members = [m.strip() for m in args.members.split(",") if m.strip()]
    unknown = [m for m in members if m not in RUNNERS]
    if unknown:
        sys.exit(f"council.py: unknown member(s): {', '.join(unknown)} (valid: {', '.join(RUNNERS)})")

    prompt = read_prompt(args)
    models = {
        "codex": args.codex_model,
        "gemini": args.gemini_model,
        "claude": args.claude_model,
        "opencode": args.opencode_model,
    }

    workdir = tempfile.mkdtemp(prefix="llm-council-")
    try:
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            futures = {m: pool.submit(dispatch, m, prompt, models[m], args.timeout, workdir) for m in members}
            results = {m: f.result() for m, f in futures.items()}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    json.dump({"members": results}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
