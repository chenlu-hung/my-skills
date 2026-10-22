#!/usr/bin/env python3
"""dispatch.py — delegate work tasks to external coding-agent CLIs in parallel.

Part of the `dispatch` Claude Code skill. Claude Code is the orchestrator: it
splits the work, writes one self-contained brief per task, and assigns each to
a worker. This script runs the workers concurrently and reports what they did.

Adapted from llm-council/council.py, with three deliberate differences:
  1. each task goes to ONE assigned worker (council broadcasts one prompt to all)
  2. workers get WRITE access to their task dir (council members are always read-only:
     in a temp dir by default, or hardened read-only in a borrowed dir via --workdir)
  3. optional git-worktree isolation so parallel tasks on the same repo don't clash

Workers authenticate through their own subscription / sign-in, not API keys:
  - codex     -> Codex CLI `codex exec -s workspace-write` (ChatGPT subscription)
  - gemini    -> Antigravity CLI `agy --dangerously-skip-permissions -p`
  - opencode  -> opencode CLI `opencode run --auto` (cwd = task dir; v2 has no --dir)

opencode falls back when it can't serve the task (model withdrawn, free quota
spent, 5xx): each free slug in OPENCODE_FREE_MODELS in turn, then agy on
OPENCODE_FALLBACK_GEMINI_MODEL. A timeout does not fall back — it already spent
the task's budget. Pinning "model" on the task tries only that slug before agy;
"fallback": false (or --no-fallback) turns the chain off.

Usage:
    python3 dispatch.py --worker codex --prompt-file brief.txt --dir .
    python3 dispatch.py --worker gemini --prompt "fix the failing tests" --worktree
    python3 dispatch.py --tasks tasks.json

tasks.json — a list of task objects:
    [{"id": "auth-fix", "worker": "codex", "task_file": "brief1.txt",
      "dir": ".", "worktree": true, "model": "", "timeout": 900, "extra_args": []},
     {"id": "docs", "worker": "opencode", "task": "...", "dir": ".", "fallback": true}]

Output: JSON on stdout:
    {"tasks": {"auth-fix": {ok, worker, report, model, elapsed_s, error,
                            dir, branch, git: {changed_files, shortstat},
                            attempts: [{worker, model, ok, error, elapsed_s}]}}}

`worker`/`model` name whoever produced the final result — after a fallback that
is not the worker the task asked for, and `attempts` lists every try in order.
`report` is the worker's final message; `git` summarizes what it actually
changed in its dir, so the orchestrator can review before merging. Worktrees
are NOT removed by this script — review, merge, then `git worktree remove`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TIMEOUT = 900  # seconds per task — work takes longer than Q&A
DEFAULT_GEMINI_MODEL = "Gemini 3.8 Flash (High)"  # `agy models` lists the live names
DEFAULT_CODEX_MODEL = "gpt-6.1-sol"  # pinned; "" = whatever the ChatGPT subscription defaults to
# Free slugs on opencode's own provider, tried in order. They come and go (the old
# opencode/deepseek-v4-flash-free was withdrawn); `opencode models | grep free` lists
# what is currently live.
OPENCODE_FREE_MODELS = ("opencode/deepseek-v4.1-flash-free", "opencode/mimo-v2.6-flash-free")
DEFAULT_OPENCODE_MODEL = OPENCODE_FREE_MODELS[0]
OPENCODE_FALLBACK_GEMINI_MODEL = "Gemini 3.8 Flash (High)"  # agy, when no opencode slug works
WORKERS = ("codex", "gemini", "opencode")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # strip terminal color codes from CLI stdout


def run_codex(t):
    """Codex CLI, workspace-write sandbox: may edit files under its dir, no network.
    `-o` writes only the final message — clean capture of the worker's report."""
    outfile = os.path.join(tempfile.mkdtemp(prefix="dispatch-codex-"), "report.txt")
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "-s", "workspace-write",
        "--cd", t["dir"],
        "-o", outfile,
    ]
    if t["model"]:
        cmd += ["-m", t["model"]]
    cmd += t["extra_args"] + [t["prompt"]]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=t["timeout"])

    report = ""
    if os.path.exists(outfile):
        with open(outfile, encoding="utf-8") as fh:
            report = fh.read().strip()
    ok = bool(report) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty report")
    return ok, report, err


def run_gemini(t):
    """Antigravity CLI `agy` in print mode, permissions auto-approved so it can edit.
    Flags must precede `-p` (Go flag parsing); cwd confines it to the task dir."""
    cmd = ["agy"]
    if t["model"]:
        cmd += ["--model", t["model"]]
    cmd += ["--dangerously-skip-permissions", "--print-timeout", f"{t['timeout']}s"]
    cmd += t["extra_args"] + ["-p", t["prompt"]]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=t["timeout"] + 30, cwd=t["dir"])

    report = ANSI.sub("", proc.stdout or "").strip()
    ok = bool(report) and proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-600:] or f"exit {proc.returncode}, empty report")
    return ok, report, err


def run_opencode(t):
    """opencode `run` in JSON mode with permissions auto-approved (`--auto`); the
    report is the concatenation of `type:text` events. opencode v2 dropped `--dir`
    and `--dangerously-skip-permissions`, so cwd is what confines it to the task dir.
    Provider failures (quota, 5xx) arrive as `type:error` events on stdout, not stderr."""
    cmd = ["opencode", "run", "--format", "json", "--auto"]
    if t["model"]:
        cmd += ["-m", t["model"]]
    cmd += t["extra_args"] + [t["prompt"]]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=t["timeout"],
                          cwd=t["dir"], stdin=subprocess.DEVNULL)

    texts, errors = [], []
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
        elif obj.get("type") == "error":
            e = obj.get("error") or {}
            errors.append(f"{e.get('type', 'error')}: {e.get('message', '')}".strip())
    report = "\n".join(texts).strip()
    ok = bool(report) and proc.returncode == 0 and not errors
    err = "" if ok else ("; ".join(errors) or ANSI.sub("", proc.stderr or "").strip()[-600:]
                         or f"exit {proc.returncode}, empty report")
    return ok, report, err


RUNNERS = {"codex": run_codex, "gemini": run_gemini, "opencode": run_opencode}
DEFAULT_MODELS = {"codex": DEFAULT_CODEX_MODEL, "gemini": DEFAULT_GEMINI_MODEL,
                  "opencode": DEFAULT_OPENCODE_MODEL}
CLI_BIN = {"codex": "codex", "gemini": "agy", "opencode": "opencode"}


def git(*args, cwd=None):
    proc = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def make_worktree(base_dir, task_id):
    """Create an isolated worktree for one task, as a sibling of the repo:
    <parent>/<repo>.dispatch/<task_id> on branch dispatch/<task_id>."""
    rc, root, err = git("-C", base_dir, "rev-parse", "--show-toplevel")
    if rc != 0:
        raise RuntimeError(f"worktree requested but {base_dir} is not a git repo: {err}")
    container = os.path.join(os.path.dirname(root), os.path.basename(root) + ".dispatch")
    os.makedirs(container, exist_ok=True)
    wt, branch = os.path.join(container, task_id), f"dispatch/{task_id}"
    if os.path.exists(wt):  # stale id from a previous run — make this one unique
        suffix = str(int(time.time()))
        wt, branch = f"{wt}-{suffix}", f"{branch}-{suffix}"
    rc, _, err = git("-C", root, "worktree", "add", "-b", branch, wt)
    if rc != 0:
        raise RuntimeError(f"git worktree add failed: {err}")
    return wt, branch


def git_summary(workdir):
    """What did the worker actually change? Porcelain status + shortstat, so the
    orchestrator can review without trusting the worker's own report."""
    rc, _, _ = git("-C", workdir, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return None
    _, status, _ = git("-C", workdir, "status", "--porcelain")
    files = status.splitlines()
    _, shortstat, _ = git("-C", workdir, "diff", "--shortstat")
    return {"changed_files": files[:100],
            "changed_count": len(files),
            "shortstat": shortstat}


def attempt_chain(t):
    """(worker, model) pairs to try in order. Only opencode has a chain: its free
    slugs (or just the pinned one), then agy on Gemini."""
    if t["worker"] != "opencode" or not t["fallback"]:
        return [(t["worker"], t["model"])]
    slugs = [t["model"]] if t["model_pinned"] else list(OPENCODE_FREE_MODELS)
    return [("opencode", m) for m in slugs] + [("gemini", OPENCODE_FALLBACK_GEMINI_MODEL)]


def run_attempt(t, worker, model):
    """One CLI run; a missing CLI / timeout / crash becomes a structured error.
    Returns (ok, report, err, timed_out)."""
    try:
        ok, report, err = RUNNERS[worker](dict(t, worker=worker, model=model))
        return ok, report, err, False
    except subprocess.TimeoutExpired:
        return False, "", f"timed out after {t['timeout']}s", True
    except FileNotFoundError:
        return False, "", (f"`{CLI_BIN[worker]}` not found on PATH — "
                           "is the CLI installed and signed in?"), False
    except Exception as exc:  # noqa: BLE001 — surface anything else as a task error
        return False, "", f"{type(exc).__name__}: {exc}", False


def dispatch(t):
    """Run one task down its attempt chain until one succeeds. A timeout stops the
    chain: the task already used its budget, and retrying would double it."""
    result = {"ok": False, "worker": t["worker"], "report": "",
              "model": t["model"] or "default", "elapsed_s": 0,
              "error": "", "dir": t["dir"], "branch": t.get("branch", ""), "git": None,
              "attempts": []}
    t0 = time.time()
    for worker, model in attempt_chain(t):
        a0 = time.time()
        ok, report, err, timed_out = run_attempt(t, worker, model)
        result["attempts"].append({"worker": worker, "model": model or "default", "ok": ok,
                                   "error": err, "elapsed_s": round(time.time() - a0, 1)})
        result.update(ok=ok, worker=worker, model=model or "default", report=report, error=err)
        if ok or timed_out:
            break
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["git"] = git_summary(t["dir"])
    return result


def load_tasks(args):
    if args.tasks:
        with open(args.tasks, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list) or not raw:
            sys.exit("dispatch.py: --tasks file must be a non-empty JSON list")
    else:
        if not args.worker:
            sys.exit("dispatch.py: give --tasks FILE, or --worker with --prompt/--prompt-file")
        raw = [{"id": args.id, "worker": args.worker, "task": args.prompt,
                "task_file": args.prompt_file, "dir": args.dir,
                "worktree": args.worktree, "model": args.model}]
    if args.no_fallback:
        for r in raw:
            r["fallback"] = False

    tasks, seen = [], set()
    for i, r in enumerate(raw, 1):
        worker = r.get("worker", "")
        if worker not in RUNNERS:
            sys.exit(f"dispatch.py: task {i}: unknown worker {worker!r} (valid: {', '.join(RUNNERS)})")
        prompt = r.get("task")
        if not prompt and r.get("task_file"):
            with open(r["task_file"], encoding="utf-8") as fh:
                prompt = fh.read()
        if not prompt or not prompt.strip():
            sys.exit(f"dispatch.py: task {i}: no task text (use \"task\" or \"task_file\")")
        task_id = r.get("id") or f"{worker}-{i}"
        if task_id in seen:
            sys.exit(f"dispatch.py: duplicate task id {task_id!r}")
        seen.add(task_id)
        model = r.get("model")
        tasks.append({
            "id": task_id, "worker": worker, "prompt": prompt,
            "dir": os.path.abspath(r.get("dir") or os.getcwd()),
            "worktree": bool(r.get("worktree")),
            "model": DEFAULT_MODELS[worker] if model is None else model,
            "model_pinned": model is not None,
            "fallback": r.get("fallback", True) is not False,
            "timeout": int(r.get("timeout") or args.timeout),
            "extra_args": list(r.get("extra_args") or []),
        })
    return tasks


def main():
    ap = argparse.ArgumentParser(description="Delegate work tasks to external coding-agent CLIs in parallel.")
    ap.add_argument("--tasks", help="JSON file with a list of task objects (see module docstring)")
    ap.add_argument("--worker", choices=WORKERS, help="single-task mode: which worker")
    ap.add_argument("--prompt", help="single-task mode: the task brief")
    ap.add_argument("--prompt-file", help="single-task mode: file containing the brief")
    ap.add_argument("--dir", default=".", help="single-task mode: directory to work in (default: cwd)")
    ap.add_argument("--worktree", action="store_true",
                    help="single-task mode: run in an isolated git worktree")
    ap.add_argument("--model", default=None, help="single-task mode: model override")
    ap.add_argument("--id", default=None, help="single-task mode: task id (default: <worker>-1)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"default per-task timeout in seconds (default: {DEFAULT_TIMEOUT})")
    ap.add_argument("--no-fallback", action="store_true",
                    help="opencode tasks: fail instead of trying the next free slug, then agy")
    args = ap.parse_args()

    tasks = load_tasks(args)

    runnable = []
    results = {}
    for t in tasks:
        if t["worktree"]:
            try:
                t["dir"], t["branch"] = make_worktree(t["dir"], t["id"])
            except RuntimeError as exc:
                results[t["id"]] = {"ok": False, "worker": t["worker"], "report": "",
                                    "model": t["model"] or "default", "elapsed_s": 0,
                                    "error": str(exc), "dir": t["dir"], "branch": "", "git": None,
                                    "attempts": []}
                continue
        runnable.append(t)

    if runnable:
        with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
            futures = {t["id"]: pool.submit(dispatch, t) for t in runnable}
            results.update({tid: f.result() for tid, f in futures.items()})

    json.dump({"tasks": results}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
