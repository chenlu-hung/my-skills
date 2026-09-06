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

# The ChatGPT bridge is not a CLI that prints prose: it speaks a JSON envelope
# ({ok, answer, elapsed_s}), so its stub reports argv from *inside* `answer` and run()
# then parses it like every other member. Without this stub the default roster resolves
# `chatgpt-ask` on the real PATH and launches ChatGPT.app in the middle of a test run.
CHATGPT_STUB = """#!/usr/bin/env python3
import sys, json
print(json.dumps({"ok": True, "elapsed_s": 0.1, "answer":
    "ARGV=" + json.dumps(sys.argv[1:]) + "\\nSTDIN=" + json.dumps(sys.stdin.read())}))
"""

failures = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stub_dir():
    d = tempfile.mkdtemp(prefix="council-stubs-")
    for name, body in (("agy", STUB), ("claude", STUB), ("codex", CODEX_STUB),
                       ("chatgpt-ask", CHATGPT_STUB)):
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


def stage1_file(dirpath, tag, ok_answers, failed=(), blank=()):
    """Write a stage-1 JSON. `ok_answers` is {member: answer}; `failed` members carry an
    error; `blank` members are ok but answered with whitespace only. `tag` keeps each
    fixture in its own file — reusing one name lets a later fixture silently replace an
    earlier one that a later assertion still refers to."""
    members = {m: {"ok": True, "answer": a, "model": "m", "elapsed_s": 1, "error": ""}
               for m, a in ok_answers.items()}
    for m in failed:
        members[m] = {"ok": False, "answer": "", "model": "m", "elapsed_s": 0, "error": "boom"}
    for m in blank:
        members[m] = {"ok": True, "answer": "   ", "model": "m", "elapsed_s": 1, "error": ""}
    path = os.path.join(dirpath, f"stage1.{tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"members": members}, fh, ensure_ascii=False)
    qpath = os.path.join(dirpath, f"q.{tag}.txt")
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
        _, mcx = run(["--prompt", "Q", "--members", "codex"], stubs)
        check("codex still sandboxed", val(mcx["codex"][0], "-s") == "read-only")

        # The roster decides which quota a council run spends, so it is worth pinning.
        # chatgpt holds the GPT seat (conversation allowance, not Codex quota) and
        # opencode is off the roster entirely.
        print("default roster:")
        check("chatgpt holds the GPT seat, not codex", sorted(m) == ["chatgpt", "claude", "gemini"],
              f"got {sorted(m)}")
        check("the chatgpt member is dispatched through the bridge",
              "--prompt-file" in m["chatgpt"][0], str(m["chatgpt"][0]))

        # ...but only the DEFAULT roster is rewritten, and only when the app cannot serve
        # the run. Getting either half wrong is silent: a member the caller never asked
        # for answers, or the desktop app answers a question about a repo it cannot see.
        print("chatgpt -> codex substitution:")
        proc, mw = run(["--workdir", repo, "--prompt", "Q"], stubs)
        check("--workdir hands the seat to codex",
              "codex" in mw and "chatgpt" not in mw, f"got {sorted(mw)}")
        check("the swap is announced on stderr, not mixed into the answers",
              "chatgpt -> codex" in proc.stderr and "chatgpt -> codex" not in proc.stdout)

        own = os.path.join(stubs, "own.schema.json")
        shutil.copy(os.path.join(HERE, "schema", "answer.schema.json"), own)
        _, msc = run(["--prompt", "Q", "--output-schema", own], stubs)
        check("an explicit --output-schema hands the seat to codex",
              "codex" in msc and "chatgpt" not in msc, f"got {sorted(msc)}")
        check("the default schema does not", "chatgpt" in m and "codex" not in m, f"got {sorted(m)}")

        _, mex = run(["--members", "chatgpt,gemini", "--workdir", repo, "--prompt", "Q"], stubs)
        check("an explicit roster is never rewritten",
              sorted(mex) == ["chatgpt", "gemini"], f"got {sorted(mex)}")
        check("the explicitly-named chatgpt refuses the borrowed dir itself",
              mex["chatgpt"][0] == [], str(mex["chatgpt"][0]))

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
        _, ms = run(["--prompt", "Q", "--members", "codex,gemini,claude"], stubs)
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

        _, mn = run(["--prompt", "Q", "--no-output-schema", "--members", "codex,gemini,claude"], stubs)
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
            s1, q = stage1_file(adir, "pair", {"codex": "ANSWER-CODEX", "claude": "ANSWER-CLAUDE"})
            proc, out = run_raw(["--anonymize", s1, "--question-file", q,
                                 "--members", "codex,claude"])
            check("2 usable answers is enough to cross-review", proc.returncode == 0, proc.stderr[:80])
            check("reports the number of responses", (out or {}).get("responses") == 2)
            prompts = (out or {}).get("review_prompts") or {}
            map_path = (out or {}).get("label_map") or ""
            check("one prompt per reviewer", sorted(prompts) == ["claude", "codex"], str(prompts))
            check("all artifacts land next to stage1.json",
                  os.path.dirname(map_path) == adir
                  and all(os.path.dirname(v) == adir for v in prompts.values()))
            texts = {r: open(v, encoding="utf-8").read() for r, v in prompts.items()}
            mapping = json.load(open(map_path, encoding="utf-8"))

            # The anonymity guarantee itself: this is what the whole staging exists for,
            # and it has to hold for EVERY reviewer's prompt, not just the first.
            check("no reviewer's prompt names a member", not any(
                n in txt for txt in texts.values()
                for n in ("codex", "claude", "gemini", "opencode", "chatgpt")))
            check("every prompt carries the question",
                  all("THE QUESTION" in txt for txt in texts.values()))
            check("every prompt carries every answer",
                  all("ANSWER-CODEX" in txt and "ANSWER-CLAUDE" in txt for txt in texts.values()))
            check("labels are A.. in order",
                  all("Response A" in txt and "Response B" in txt for txt in texts.values()))

            # The point of Change 1: reviewers must NOT share an ordering, or their
            # position bias stays correlated across the council.
            check("reviewers get different orderings", texts["codex"] != texts["claude"],
                  "both reviewers saw the same Response A")
            check("label map is nested per reviewer",
                  sorted(mapping) == ["claude", "codex"], str(mapping))
            check("each reviewer's map covers the same members",
                  all(sorted(mapping[r].values()) == ["claude", "codex"] for r in mapping),
                  str(mapping))
            check("each reviewer's map keys are its labels",
                  all(sorted(mapping[r]) == ["A", "B"] for r in mapping), str(mapping))
            check("the two reviewers' maps actually differ",
                  mapping["codex"] != mapping["claude"], str(mapping))

            # Dropouts are reported but never labelled.
            s1b, qb = stage1_file(adir, "drop", {"codex": "A1", "claude": "A2", "gemini": "A3"},
                                  failed=("opencode",), blank=("chatgpt",))
            proc, out = run_raw(["--anonymize", s1b, "--question-file", qb,
                                 "--members", "codex,claude"])
            check("failed and blank members are reported as dropouts",
                  sorted((out or {}).get("dropouts") or []) == ["chatgpt", "opencode"], str(out))
            check("only usable answers are labelled", (out or {}).get("responses") == 3)

            # Cardinality guards.
            s1c, qc = stage1_file(adir, "one", {"codex": "ONLY"})
            proc, _ = run_raw(["--anonymize", s1c, "--question-file", qc, "--members", "codex,claude"])
            check("1 usable answer is refused", proc.returncode != 0)
            check("the 1-answer message says an answer survived",
                  "1 usable answer" in proc.stderr, proc.stderr[:120])

            s1d, qd = stage1_file(adir, "none", {}, failed=("codex", "claude"))
            proc, _ = run_raw(["--anonymize", s1d, "--question-file", qd, "--members", "codex,claude"])
            check("0 usable answers is refused", proc.returncode != 0)
            check("the 0-answer message does not tell the caller to synthesize",
                  "no usable answers" in proc.stderr and "synthesize directly" not in proc.stderr,
                  proc.stderr[:120])

            s1e, qe = stage1_file(adir, "cap26", {f"m{i}": f"A{i}" for i in range(26)})
            proc, out = run_raw(["--anonymize", s1e, "--question-file", qe, "--members", "codex,claude"])
            check("26 answers still label A-Z", proc.returncode == 0 and (out or {}).get("responses") == 26,
                  proc.stderr[:80])
            s1f, qf = stage1_file(adir, "cap27", {f"m{i}": f"A{i}" for i in range(27)})
            proc, _ = run_raw(["--anonymize", s1f, "--question-file", qf, "--members", "codex,claude"])
            check("27 answers are refused", proc.returncode != 0 and "too many" in proc.stderr,
                  proc.stderr[:80])

            proc, _ = run_raw(["--anonymize", s1, "--prompt", "x"])
            check("--anonymize without --question-file is rejected",
                  proc.returncode != 0 and "requires --question-file" in proc.stderr)

            # A reviewer need not have answered: the roster is named, not derived.
            proc, out = run_raw(["--anonymize", s1, "--question-file", q, "--members", "gemini"])
            check("a reviewer absent from stage 1 still gets a prompt",
                  proc.returncode == 0 and sorted((out or {}).get("review_prompts") or {}) == ["gemini"],
                  str(out))

            # Roster validation has to happen BEFORE any member-derived filename is written.
            before = {f for f in os.listdir(adir) if f.startswith("review_prompt.")}
            proc, _ = run_raw(["--anonymize", s1, "--question-file", q, "--members", "nosuch"])
            check("an unknown reviewer is rejected by --anonymize",
                  proc.returncode != 0 and "unknown member" in proc.stderr, proc.stderr[:80])
            after = {f for f in os.listdir(adir) if f.startswith("review_prompt.")}
            check("a rejected roster writes no prompt files", after == before, str(after - before))
            proc, _ = run_raw(["--anonymize", s1, "--question-file", q, "--members", ",,"])
            check("an empty roster is rejected",
                  proc.returncode != 0 and "--members is empty" in proc.stderr, proc.stderr[:80])
            proc, out = run_raw(["--anonymize", s1, "--question-file", q,
                                 "--members", "codex,codex,claude"])
            check("a duplicated reviewer is deduplicated",
                  (out or {}).get("reviewers") == ["codex", "claude"], str(out))
        finally:
            shutil.rmtree(adir, ignore_errors=True)

        print("aggregate (alignment, self-preference, degradation):")
        gdir = tempfile.mkdtemp(prefix="council-agg-")
        try:
            g1, gq = stage1_file(gdir, "agg", {"codex": "AAA", "gemini": "BBB", "claude": "CCC"})
            crit = os.path.join(gdir, "crit.txt")
            with open(crit, "w", encoding="utf-8") as fh:
                fh.write("correctness\ndepth\n")
            proc, out = run_raw(["--anonymize", g1, "--question-file", gq,
                                 "--members", "codex,claude", "--criteria", crit])
            check("criteria reach every review prompt", proc.returncode == 0 and all(
                "- correctness" in open(v, encoding="utf-8").read()
                for v in (out or {}).get("review_prompts", {}).values()), proc.stderr[:80])
            check("the prompt no longer asks for a ranking", all(
                "rank them best-to-worst" not in open(v, encoding="utf-8").read()
                for v in (out or {}).get("review_prompts", {}).values()))
            gmap = (out or {}).get("label_map")

            def stage2(reviewer, prefs, errors_for=None):
                labels = json.load(open(gmap, encoding="utf-8"))[reviewer]
                reviews = [{"label": lb,
                            "scores": [{"criterion": "correctness", "score": prefs[m][0], "reason": "r"},
                                       {"criterion": "depth", "score": prefs[m][1], "reason": "r"}],
                            "factual_errors": ([f"{m} said something false"]
                                               if m == errors_for else [])}
                           for lb, m in labels.items()]
                with open(os.path.join(gdir, f"stage2.{reviewer}.json"), "w", encoding="utf-8") as fh:
                    json.dump({"members": {reviewer: {"ok": True, "answer": "x", "structured":
                                                      {"reviews": reviews}, "model": "m",
                                                      "elapsed_s": 1, "error": ""}}}, fh)

            # codex rates its own answer far above everything else.
            stage2("codex", {"codex": (99, 99), "gemini": (60, 60), "claude": (62, 62)})
            stage2("claude", {"codex": (61, 61), "gemini": (64, 64), "claude": (95, 95)})
            proc, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("aggregate succeeds with two reviewers",
                  proc.returncode == 0 and (agg or {}).get("usable_reviewers") == 2, proc.stderr[:100])
            check("each reviewer's own answer is excluded", all(
                (agg or {})["reviewers"][r].get("own_answer_excluded") for r in ("codex", "claude")))
            check("a self-rated answer does not win on its own vote",
                  (agg or {})["ranking"][0] != "codex", str((agg or {}).get("ranking")))
            check("codex's score comes only from the other reviewer",
                  list((agg or {})["responses"]["codex"]["scored_by"]) == ["claude"],
                  str((agg or {})["responses"]["codex"]["scored_by"]))
            check("an answer scored by both reviewers reports a spread",
                  (agg or {})["responses"]["gemini"]["spread"] is not None)
            check("an answer scored once reports no spread",
                  (agg or {})["responses"]["codex"]["spread"] is None)

            # The gate: a quoted factual error is always enough on its own.
            stage2("claude", {"codex": (61, 61), "gemini": (64, 64), "claude": (95, 95)},
                   errors_for="gemini")
            _, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("a quoted factual error trips the gate",
                  (agg or {})["gate"]["rebuttal_recommended"]
                  and "gemini" in (agg or {})["factual_errors"], str((agg or {}).get("gate")))

            # Degradation: each of these must cost one reviewer, never the whole run.
            stage2("claude", {"codex": (61, 61), "gemini": (64, 64), "claude": (95, 95)})
            os.rename(os.path.join(gdir, "stage2.claude.json"), os.path.join(gdir, "_held.json"))
            proc, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("a missing stage-2 file costs only that reviewer",
                  proc.returncode == 0 and (agg or {}).get("usable_reviewers") == 1
                  and not (agg or {})["reviewers"]["claude"]["ok"], str((agg or {}).get("reviewers")))
            os.rename(os.path.join(gdir, "_held.json"), os.path.join(gdir, "stage2.claude.json"))

            with open(os.path.join(gdir, "stage2.claude.json"), "w", encoding="utf-8") as fh:
                json.dump({"members": {"claude": {"ok": True, "answer": "prose", "structured": None,
                                                  "model": "m", "elapsed_s": 1, "error": ""}}}, fh)
            _, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("a reviewer that ignored the schema is excluded numerically, prose kept",
                  (agg or {})["reviewers"]["claude"].get("prose_retained") is True,
                  str((agg or {})["reviewers"]["claude"]))

            labels = json.load(open(gmap, encoding="utf-8"))["claude"]
            bad = [{"label": lb, "scores": [{"criterion": "correctness", "score": 70, "reason": "r"},
                                            {"criterion": "elegance", "score": 60, "reason": "r"}],
                    "factual_errors": []} for lb in labels]
            with open(os.path.join(gdir, "stage2.claude.json"), "w", encoding="utf-8") as fh:
                json.dump({"members": {"claude": {"ok": True, "answer": "x",
                                                  "structured": {"reviews": bad}, "model": "m",
                                                  "elapsed_s": 1, "error": ""}}}, fh)
            _, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("an invented criterion is rejected by name",
                  "elegance" in (agg or {})["reviewers"]["claude"]["reason"],
                  str((agg or {})["reviewers"]["claude"]))

            os.remove(os.path.join(gdir, "stage2.codex.json"))
            os.remove(os.path.join(gdir, "stage2.claude.json"))
            proc, agg = run_raw(["--aggregate", gmap, "--criteria", crit])
            check("zero usable reviewers fails loudly rather than ranking nothing",
                  proc.returncode != 0 and "cross-review did not run" in json.dumps(agg or {}),
                  str(agg)[:120])

            proc, _ = run_raw(["--aggregate", gmap, "--anonymize", g1, "--question-file", gq])
            check("--anonymize and --aggregate together are refused",
                  proc.returncode != 0 and "separate stages" in proc.stderr, proc.stderr[:80])
        finally:
            shutil.rmtree(gdir, ignore_errors=True)

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
