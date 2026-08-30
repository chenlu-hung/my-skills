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
open(a[a.index("-o") + 1], "w").write(
    "ARGV=" + json.dumps(a) + "\\nSTDIN=" + json.dumps(sys.stdin.read()))
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


def run(args, stubs, stdin_text=""):
    env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"])
    proc = subprocess.run([sys.executable, COUNCIL, *args], capture_output=True, text=True,
                          env=env, input=stdin_text)
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


def run_raw(args):
    """Run council.py and return (proc, parsed stdout JSON or None). For modes like
    --anonymize that do not dispatch and so emit no `members` envelope."""
    proc = subprocess.run([sys.executable, COUNCIL, *args], capture_output=True, text=True)
    try:
        return proc, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc, None


def stage1_file(dirpath, ok_answers, failed=(), blank=()):
    """Write a stage-1 JSON. `ok_answers` is {member: answer}; `failed` members carry an
    error; `blank` members are ok but answered with whitespace only."""
    members = {m: {"ok": True, "answer": a, "model": "m", "elapsed_s": 1, "error": ""}
               for m, a in ok_answers.items()}
    for m in failed:
        members[m] = {"ok": False, "answer": "", "model": "m", "elapsed_s": 0, "error": "boom"}
    for m in blank:
        members[m] = {"ok": True, "answer": "   ", "model": "m", "elapsed_s": 1, "error": ""}
    path = os.path.join(dirpath, "stage1.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"members": members}, fh, ensure_ascii=False)
    qpath = os.path.join(dirpath, "q.txt")
    with open(qpath, "w", encoding="utf-8") as fh:
        fh.write("THE QUESTION")
    return path, qpath


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

        # Every CLI child must get stdin=DEVNULL. `codex exec` with an inherited pipe on
        # stdin prints "Reading additional input from stdin..." and waits for a second
        # prompt, truncating the turn: no `-o` file, and `codex doctor` stays all green.
        # Feed the parent a non-empty stdin the way an agent harness does, and check none
        # of it leaks into the children.
        print("stdin isolation (codex exec truncates on an inherited pipe):")
        # One member at a time: members run concurrently, so whichever child reads the
        # inherited pipe first drains it — testing them together makes the leak
        # nondeterministic and the assertion useless.
        _, mc = run(["--prompt", "Q", "--members", "codex"], stubs, stdin_text="LEAKED-STDIN")
        check("codex gets a closed stdin", mc["codex"][1] == "", repr(mc["codex"][1]))
        _, mg = run(["--prompt", "Q", "--members", "gemini"], stubs, stdin_text="LEAKED-STDIN")
        check("gemini gets a closed stdin", mg["gemini"][1] == "", repr(mg["gemini"][1]))
        _, ml = run(["--prompt", "Q", "--members", "claude"], stubs, stdin_text="LEAKED-STDIN")
        check("claude still receives the prompt on stdin (deliberate)",
              ml["claude"][1] == "Q", repr(ml["claude"][1]))

        # Structured output is ON by default and must reach every member in the form its
        # CLI accepts: codex/agy take a FILE, claude takes an inline JSON STRING. Getting the
        # form wrong is silent — the CLI errors and the member just comes back empty.
        print("structured output (default on):")
        _, ms = run(["--prompt", "Q"], stubs)
        cdx, gem, cld = ms["codex"][0], ms["gemini"][0], ms["claude"][0]
        check("codex gets --output-schema as a file path",
              (val(cdx, "--output-schema") or "").endswith(".json"), repr(val(cdx, "--output-schema")))
        check("agy gets --json-schema as a file path",
              (val(gem, "--json-schema") or "").endswith(".json"), repr(val(gem, "--json-schema")))
        check("agy also gets --output-format json (it rejects the schema otherwise)",
              val(gem, "--output-format") == "json", repr(val(gem, "--output-format")))
        check("claude gets --json-schema inline, not a path",
              (val(cld, "--json-schema") or "").startswith("{"), repr(val(cld, "--json-schema"))[:60])
        check("claude keeps --disallowedTools last even with a schema",
              "--disallowedTools" not in cld or cld[-4:] == ["Write", "Edit", "NotebookEdit", "Bash"])

        _, mn = run(["--prompt", "Q", "--no-output-schema"], stubs)
        check("--no-output-schema drops it from every member",
              not any(f in mn[m][0] for m, f in (("codex", "--output-schema"),
                                                ("gemini", "--json-schema"),
                                                ("claude", "--json-schema"))))

        # A member that answers in schema-shaped JSON must be rendered back to prose: the
        # chair's synthesis and --anonymize both read `answer` as text.
        print("structured answers are rendered back to Markdown:")
        sd = stub_dir()
        with open(os.path.join(sd, "claude"), "w") as fh:
            fh.write('#!/usr/bin/env python3\n'
                     'import json,sys; sys.stdin.read()\n'
                     'print(json.dumps({"answer":"BODY","key_points":["K1","K2"],'
                     '"confidence":"high","caveats":["C1"]}, ensure_ascii=False))\n')
        os.chmod(os.path.join(sd, "claude"), 0o755)
        proc, _ = run(["--prompt", "Q", "--members", "claude"], sd)
        res = json.loads(proc.stdout)["members"]["claude"]
        check("prose body survives", "BODY" in res["answer"], res["answer"][:60])
        check("key_points rendered as bullets", "- K1" in res["answer"] and "- K2" in res["answer"])
        check("caveats rendered", "- C1" in res["answer"])
        check("confidence rendered", "high" in res["answer"])
        check("raw object kept under `structured`",
              (res.get("structured") or {}).get("key_points") == ["K1", "K2"])

        # A member that ignores the schema must still contribute, not be dropped.
        sd2 = stub_dir()
        with open(os.path.join(sd2, "claude"), "w") as fh:
            fh.write('#!/usr/bin/env python3\n'
                     'import sys; sys.stdin.read(); print("PLAIN PROSE")\n')
        os.chmod(os.path.join(sd2, "claude"), 0o755)
        proc2, _ = run(["--prompt", "Q", "--members", "claude"], sd2)
        res2 = json.loads(proc2.stdout)["members"]["claude"]
        check("non-JSON answer passes through unchanged", res2["answer"] == "PLAIN PROSE", res2["answer"][:40])
        check("structured is None when the member ignored the schema", res2.get("structured") is None)

        print("pinned reviewer models (the roster decision, not a default):")
        _, mm = run(["--prompt", "Q", "--members", "codex,claude"], stubs)
        check("codex is pinned to gpt-5.6-sol", val(mm["codex"][0], "-m") == "gpt-5.6-sol",
              str(mm["codex"][0][:10]))
        check("claude is pinned to claude-opus-5", val(mm["claude"][0], "--model") == "claude-opus-5",
              str(mm["claude"][0][:6]))

        print("anonymize (characterisation — pins behaviour a rewrite must preserve):")
        adir = tempfile.mkdtemp(prefix="council-anon-")
        try:
            s1, q = stage1_file(adir, {"codex": "ANSWER-CODEX", "claude": "ANSWER-CLAUDE"})
            proc, out = run_raw(["--anonymize", s1, "--question-file", q])
            check("2 usable answers is enough to cross-review", proc.returncode == 0, proc.stderr[:80])
            check("reports the number of responses", (out or {}).get("responses") == 2)
            prompt_path = (out or {}).get("review_prompt") or ""
            map_path = (out or {}).get("label_map") or ""
            check("both artifacts land next to stage1.json",
                  os.path.dirname(prompt_path) == adir and os.path.dirname(map_path) == adir)
            prompt = open(prompt_path, encoding="utf-8").read()
            mapping = json.load(open(map_path, encoding="utf-8"))

            # The anonymity guarantee itself: this is what the whole staging exists for.
            check("review prompt names no member", not any(
                n in prompt for n in ("codex", "claude", "gemini", "opencode", "chatgpt")), prompt[:80])
            check("review prompt carries the question", "THE QUESTION" in prompt)
            check("review prompt carries every answer",
                  "ANSWER-CODEX" in prompt and "ANSWER-CLAUDE" in prompt)
            check("labels are A.. in order", "Response A" in prompt and "Response B" in prompt)
            check("label map covers exactly the usable members",
                  sorted(mapping.values()) == ["claude", "codex"], str(mapping))
            check("label map keys are the labels used", sorted(mapping) == ["A", "B"], str(mapping))

            # Dropouts are reported but never labelled.
            s1b, qb = stage1_file(adir, {"codex": "A1", "claude": "A2", "gemini": "A3"},
                                  failed=("opencode",), blank=("chatgpt",))
            proc, out = run_raw(["--anonymize", s1b, "--question-file", qb])
            check("failed and blank members are reported as dropouts",
                  sorted((out or {}).get("dropouts") or []) == ["chatgpt", "opencode"], str(out))
            check("only usable answers are labelled", (out or {}).get("responses") == 3)

            # Cardinality guards.
            s1c, qc = stage1_file(adir, {"codex": "ONLY"})
            proc, _ = run_raw(["--anonymize", s1c, "--question-file", qc])
            check("1 usable answer is refused", proc.returncode != 0)
            check("the 1-answer message says an answer survived",
                  "1 usable answer" in proc.stderr, proc.stderr[:120])

            s1d, qd = stage1_file(adir, {}, failed=("codex", "claude"))
            proc, _ = run_raw(["--anonymize", s1d, "--question-file", qd])
            check("0 usable answers is refused", proc.returncode != 0)
            check("the 0-answer message does not tell the caller to synthesize",
                  "no usable answers" in proc.stderr and "synthesize directly" not in proc.stderr,
                  proc.stderr[:120])

            s1e, qe = stage1_file(adir, {f"m{i}": f"A{i}" for i in range(26)})
            proc, out = run_raw(["--anonymize", s1e, "--question-file", qe])
            check("26 answers still label A-Z", proc.returncode == 0 and (out or {}).get("responses") == 26,
                  proc.stderr[:80])
            s1f, qf = stage1_file(adir, {f"m{i}": f"A{i}" for i in range(27)})
            proc, _ = run_raw(["--anonymize", s1f, "--question-file", qf])
            check("27 answers are refused", proc.returncode != 0 and "too many" in proc.stderr,
                  proc.stderr[:80])

            proc, _ = run_raw(["--anonymize", s1, "--prompt", "x"])
            check("--anonymize without --question-file is rejected",
                  proc.returncode != 0 and "requires --question-file" in proc.stderr)
        finally:
            shutil.rmtree(adir, ignore_errors=True)

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
