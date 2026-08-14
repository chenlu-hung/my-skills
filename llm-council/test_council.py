#!/usr/bin/env python3
"""Regression tests for council.py's borrowed-workdir mode (`--workdir`).

Run: python3 llm-council/test_council.py

These cover the parts where a bug is expensive rather than annoying: deleting a directory
the caller owns, leaking files into their repo, or silently dropping one of the read-only
flags that are the only thing keeping a member from editing their code.

No member CLI is ever invoked for real — each is replaced by a stub on PATH that echoes back
the argv it was handed, so the assertions are about the command we *construct*. The stubs
report argv as JSON: a shell `echo "$@"` silently drops empty arguments, which would make
`--setting-sources ""` (the flag that stops a borrowed repo's hooks from firing) untestable.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COUNCIL = os.path.join(HERE, "council.py")

STUB = """#!/usr/bin/env python3
import sys, json
print("ARGV=" + json.dumps(sys.argv[1:]) + "\\nSTDIN=" + json.dumps(sys.stdin.read()))
"""

# codex is the odd one out: it reports through the file given to `-o`, not stdout.
CODEX_STUB = """#!/usr/bin/env python3
import sys, json
a = sys.argv[1:]
open(a[a.index("-o") + 1], "w").write("ARGV=" + json.dumps(a) + "\\nSTDIN=\\"\\"")
"""

failures = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stub_dir():
    d = tempfile.mkdtemp(prefix="council-stubs-")
    for name, body in (("agy", STUB), ("claude", STUB), ("codex", CODEX_STUB)):
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
    return d


def run(args, stubs):
    env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"])
    proc = subprocess.run([sys.executable, COUNCIL, *args], capture_output=True, text=True, env=env)
    members = json.loads(proc.stdout)["members"] if proc.stdout.strip().startswith("{") else {}
    parsed = {}
    for name, res in members.items():
        argv, stdin = [], ""
        for line in (res.get("answer") or "").splitlines():
            if line.startswith("ARGV="):
                argv = json.loads(line[5:])
            elif line.startswith("STDIN="):
                stdin = json.loads(line[6:])
        parsed[name] = (argv, stdin)
    return proc, parsed


def val(argv, flag):
    """Value following `flag`, or None if the flag is absent."""
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def main():
    stubs = stub_dir()
    repo = tempfile.mkdtemp(prefix="council-fake-repo-")
    canary = os.path.join(repo, "keepme.txt")
    with open(canary, "w") as fh:
        fh.write("precious")

    try:
        print("borrowed workdir (--workdir):")
        _, m = run(["--workdir", repo, "--prompt", "REVIEW"], stubs)
        codex, gemini, claude = m["codex"][0], m["gemini"][0], m["claude"][0]

        # The whole point of the flag: members must run *in* the caller's directory.
        check("codex runs in the borrowed dir", val(codex, "--cd") == repo)

        # Safety: the caller's directory and its contents must survive untouched.
        check("borrowed dir is not deleted", os.path.isdir(repo))
        check("existing file untouched", os.path.exists(canary))
        check("no stray files left in borrowed dir", sorted(os.listdir(repo)) == ["keepme.txt"],
              f"found {sorted(os.listdir(repo))}")
        check("codex -o target is outside the borrowed dir",
              not (val(codex, "-o") or "").startswith(repo))

        # Read-only hardening, per member. Losing any of these silently hands a
        # reviewer write access to the user's repo.
        check("codex keeps its read-only sandbox", val(codex, "-s") == "read-only")
        check("gemini gets plan mode", val(gemini, "--mode") == "plan")
        check("claude denies the write tools",
              all(t in claude for t in ("Write", "Edit", "NotebookEdit", "Bash")))
        check("claude loads no settings (a borrowed repo's hooks must not fire)",
              val(claude, "--setting-sources") == "")

        # --disallowedTools is variadic: anything after it is eaten as a tool name.
        check("claude keeps --disallowedTools last", claude[-4:] == ["Write", "Edit", "NotebookEdit", "Bash"])
        check("claude receives the prompt on stdin", m["claude"][1] == "REVIEW")

        # agy's own print deadline is independent of our subprocess timeout.
        _, m2 = run(["--workdir", repo, "--timeout", "900", "--members", "gemini", "--prompt", "x"], stubs)
        check("gemini is given an explicit print-timeout", val(m2["gemini"][0], "--print-timeout") == "900s")

        print("default temp-dir mode (llm-council's path — must be unchanged):")
        _, m = run(["--prompt", "Q"], stubs)
        check("no plan mode", "--mode" not in m["gemini"][0])
        check("no tool denials", "--disallowedTools" not in m["claude"][0])
        check("project settings still loaded", val(m["claude"][0], "--setting-sources") == "project")
        check("codex still sandboxed", val(m["codex"][0], "-s") == "read-only")

        print("argument validation:")
        proc, _ = run(["--workdir", os.path.join(repo, "nope"), "--prompt", "x"], stubs)
        check("missing --workdir dir is rejected", proc.returncode != 0 and "not a directory" in proc.stderr)
        proc, _ = run(["--members", "nosuchmodel", "--prompt", "x"], stubs)
        check("unknown member is rejected", proc.returncode != 0 and "unknown member" in proc.stderr)
    finally:
        shutil.rmtree(stubs, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
