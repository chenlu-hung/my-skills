#!/usr/bin/env python3
"""
project-map — build/update a compact, on-demand project map for coding agents.

This is the deterministic "mechanical" layer of the project-map skill. It runs
universal-ctags to index symbols, groups source files into modules, extracts
imports/dependencies, and emits skeleton Markdown under <project>/.projectmap/
for an agent to fill with short semantic summaries. It is meant to be re-run
incrementally (`update`) so only changed modules need re-summarizing.

The script NEVER writes the human/agent-authored prose: it only (re)generates
the blocks between the `projectmap:*` markers, plus `tags` and `manifest.json`.
Everything between/outside those markers (the `## Summary` sections, the prose
in ARCHITECTURE.md) is preserved across runs.

Usage:
    build-map.py [build|update|status] [PROJECT_PATH]

    build    full reindex; refresh every module's auto block (summaries kept)
    update   incremental; refresh only changed/new modules
    status   read-only drift report (no ctags, no writes)

Env:
    PROJECTMAP_DEPTH   module grouping depth (default 2)

For languages stock Universal Ctags can't parse (e.g. Swift), a bundled regex
optlib under parsers/ is auto-loaded — but only when ctags reports no native
parser, so it stays conflict-free if upstream adds one later.

Requires: python3 (stdlib only) and universal-ctags on PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = ".projectmap"

# This script's own directory, so we can find bundled assets (parsers/).
SKILL_DIR = Path(__file__).resolve().parent
PARSERS_DIR = SKILL_DIR / "parsers"

# Languages stock Universal Ctags doesn't parse, for which we ship a regex
# optlib. Loaded via `--options=` only when ctags lacks native support, so it
# stays inert (and conflict-free) if upstream ctags later adds the language.
# ext -> (language name as printed by `ctags --list-languages`, optlib filename)
BUNDLED_PARSERS = {
    ".swift": ("Swift", "swift.ctags"),
}

# Extensions fed to ctags. The primary stack (C/C++/Python/Rust/Swift) plus a
# handful of common languages so the skill is reusable on other repos.
EXTS = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".C", ".H",
    ".py", ".pyi", ".rs", ".swift",
    ".go", ".java", ".js", ".jsx", ".ts", ".tsx", ".kt", ".rb", ".cs", ".m", ".mm",
}

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "target", "build", "dist", "out",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".build", ".swiftpm", "Pods", "DerivedData", "vendor", "third_party", OUT_DIR,
}

# ctags kinds worth surfacing as a module's "public surface". Locals, members,
# fields, parameters and variables are intentionally dropped to keep it compact.
SIGNIFICANT_KINDS = {
    "function", "method", "class", "struct", "enum", "interface", "trait",
    "protocol", "namespace", "module", "typedef", "macro", "union", "prototype",
    "package", "constructor", "type", "extension", "typealias", "actor",
}

AUTO_START = "<!-- projectmap:auto:start (generated — do not edit by hand) -->"
AUTO_END = "<!-- projectmap:auto:end -->"
MOD_START = "<!-- projectmap:modules:start (generated — do not edit by hand) -->"
MOD_END = "<!-- projectmap:modules:end -->"
SUMMARY_TODO = "_TODO: summarize this module (~3 sentences) — what it does, why it exists, its role._"
ONELINER_TODO = "_TODO_"

C_EXTS = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".C", ".H", ".m", ".mm"}


def die(msg: str) -> None:
    sys.stderr.write(f"[project-map] error: {msg}\n")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def git_files(root: Path):
    try:
        base = ["git", "-c", "core.quotepath=false", "ls-files"]
        tracked = subprocess.run(base, cwd=root, text=True, capture_output=True, check=True).stdout.splitlines()
        others = subprocess.run(base + ["--others", "--exclude-standard"], cwd=root, text=True, capture_output=True, check=True).stdout.splitlines()
        return tracked + others
    except Exception:
        return None


def walk_files(root: Path):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            rel = (Path(dirpath) / fn).relative_to(root)
            found.append(str(rel).replace(os.sep, "/"))
    return found


def list_source_files(root: Path):
    raw = git_files(root)
    used_git = raw is not None
    if raw is None:
        raw = walk_files(root)
    out = []
    for f in raw:
        if not f:
            continue
        if any(seg in EXCLUDE_DIRS for seg in f.split("/")):
            continue
        if Path(f).suffix not in EXTS:
            continue
        if not (root / f).is_file():
            continue
        out.append(f)
    return sorted(set(out)), used_git


def module_key(rel: str, depth: int) -> str:
    parts = [p for p in Path(rel).parent.parts if p != "."]
    if not parts:
        return "(root)"
    return "/".join(parts[:depth])


def safe_name(key: str) -> str:
    if key == "(root)":
        return "_root"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key.replace("/", "__"))


# --------------------------------------------------------------------------- #
# ctags
# --------------------------------------------------------------------------- #
def check_ctags():
    try:
        v = subprocess.run(["ctags", "--version"], text=True, capture_output=True)
    except FileNotFoundError:
        die("universal-ctags not found. Install it (macOS: `brew install universal-ctags`; "
            "Debian/Ubuntu: `apt install universal-ctags`).")
    if "Universal Ctags" not in (v.stdout or ""):
        sys.stderr.write("[project-map] warning: ctags does not look like Universal Ctags; "
                         "symbol extraction may be degraded.\n")


def ctags_languages():
    """Languages ctags can parse natively (names from `--list-languages`)."""
    try:
        out = subprocess.run(["ctags", "--list-languages"], text=True, capture_output=True).stdout
    except Exception:
        return set()
    # Lines may carry a trailing tag like "[disabled]"; the name is the 1st token.
    return {line.split()[0] for line in out.splitlines() if line.strip()}


def optlib_options(files):
    """`--options=` args for bundled parsers covering languages ctags lacks.

    Returns (options, used) where `used` maps language name -> optlib path, for
    reporting. Only languages actually present in `files` and missing from
    native ctags support get a bundled parser injected.
    """
    exts = {Path(f).suffix for f in files}
    native = ctags_languages()
    options, used = [], {}
    for ext, (lang, fname) in BUNDLED_PARSERS.items():
        if ext not in exts or lang in native:
            continue
        opt = PARSERS_DIR / fname
        if opt.exists():
            options.append(f"--options={opt}")
            used[lang] = opt
        else:
            sys.stderr.write(f"[project-map] warning: bundled {lang} parser missing at {opt}; "
                             f"{lang} symbols will be empty.\n")
    return options, used


def warn_empty_languages(root: Path, files, by_file) -> None:
    """Surface silent symbol-extraction failures.

    An extension present in many files but yielding zero significant symbols
    usually means ctags has no parser for that language (the failure is
    otherwise invisible — the docs just say `Public symbols (0)`).
    """
    sig_by_ext, files_by_ext = {}, {}
    for f in files:
        ext = Path(f).suffix
        files_by_ext[ext] = files_by_ext.get(ext, 0) + 1
        sig_by_ext.setdefault(ext, 0)
        for kind, _name, _ln in by_file.get(f.replace(os.sep, "/"), []):
            if kind in SIGNIFICANT_KINDS:
                sig_by_ext[ext] += 1
    blind = sorted(ext for ext, n in files_by_ext.items() if n >= 3 and sig_by_ext.get(ext, 0) == 0)
    if blind:
        sys.stderr.write(
            "[project-map] warning: 0 symbols extracted for " + ", ".join(blind) +
            " — ctags likely has no parser for these. The map's `Public symbols` "
            "will be empty for those modules.\n")


def run_ctags(root: Path, files, tags_path: Path, extra_options=None) -> None:
    cmd = ["ctags", *(extra_options or []),
           "--excmd=number", "--fields=+K", "--sort=no", "-L", "-", "-f", str(tags_path)]
    proc = subprocess.run(cmd, input="\n".join(files), text=True, cwd=root, capture_output=True)
    if not tags_path.exists():
        die(f"ctags produced no tags file.\n{proc.stderr.strip()}")


def parse_tags(tags_path: Path):
    by_file = {}
    with tags_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("!_TAG"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            name, fname, addr, kind = parts[0], parts[1].replace(os.sep, "/"), parts[2], parts[3]
            # With --excmd=number in extended format the address is the line
            # number followed by the field terminator, e.g. `26;"` — take the
            # leading integer.
            m = re.match(r"\d+", addr)
            lineno = int(m.group()) if m else 0
            by_file.setdefault(fname, []).append((kind, name, lineno))
    return by_file


# --------------------------------------------------------------------------- #
# Per-module extraction
# --------------------------------------------------------------------------- #
def hash_module(root: Path, files) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(f.encode())
        h.update(b"\0")
        try:
            h.update((root / f).read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()


def module_symbols(by_file, files):
    syms = []
    for f in sorted(files):
        for kind, name, lineno in by_file.get(f, []):
            if kind in SIGNIFICANT_KINDS:
                syms.append((f, lineno, kind, name))
    syms.sort(key=lambda x: (x[0], x[1]))
    return syms


def gather_deps(root: Path, files):
    deps = set()
    for f in files:
        ext = Path(f).suffix
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if ext in C_EXTS:
                m = re.match(r'#\s*include\s+[<"]([^>"]+)[>"]', s)
                if m:
                    deps.add(m.group(1))
            elif ext in {".py", ".pyi"}:
                m = re.match(r'(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))', s)
                if m:
                    deps.add((m.group(1) or m.group(2)).split(".")[0])
            elif ext == ".rs":
                m = re.match(r'use\s+([A-Za-z0-9_:]+)', s)
                if m:
                    deps.add(m.group(1).split("::")[0])
                m = re.match(r'extern\s+crate\s+([A-Za-z0-9_]+)', s)
                if m:
                    deps.add(m.group(1))
            elif ext == ".swift":
                m = re.match(r'import\s+([A-Za-z0-9_.]+)', s)
                if m:
                    deps.add(m.group(1))
            elif ext in {".ts", ".tsx", ".js", ".jsx"}:
                m = re.search(r"""from\s+['"]([^'"]+)['"]""", s)
                if m:
                    deps.add(m.group(1))
            elif ext == ".go":
                m = re.match(r'import\s+"([^"]+)"', s)
                if m:
                    deps.add(m.group(1))
            elif ext in {".java", ".kt"}:
                m = re.match(r'import\s+([A-Za-z0-9_.]+)', s)
                if m:
                    deps.add(m.group(1))
    return sorted(deps)


def detect_build(root: Path):
    cmds = []
    has = lambda p: (root / p).exists()
    if has("Cargo.toml"):
        cmds += [("build", "cargo build"), ("test", "cargo test")]
    if has("pyproject.toml") or has("setup.py"):
        cmds += [("install", "pip install -e ."), ("test", "pytest")]
    if has("CMakeLists.txt"):
        cmds += [("build", "cmake -B build && cmake --build build")]
    if has("Makefile") or has("makefile"):
        cmds += [("build", "make"), ("test", "make test")]
    if has("Package.swift"):
        cmds += [("build", "swift build"), ("test", "swift test")]
    if has("meson.build"):
        cmds += [("build", "meson setup build && meson compile -C build")]
    if has("package.json"):
        cmds += [("install", "npm install"), ("test", "npm test")]
    if has("go.mod"):
        cmds += [("build", "go build ./..."), ("test", "go test ./...")]
    return cmds


def detect_entries(root: Path, files):
    entries = []
    for f in files:
        ext, name = Path(f).suffix, Path(f).name
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = False
        if ext == ".rs" and re.search(r"\bfn\s+main\s*\(", text):
            hit = True
        elif ext in C_EXTS and re.search(r"\b(?:int|void)\s+main\s*\(", text):
            hit = True
        elif ext in {".py", ".pyi"} and (name == "__main__.py" or re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", text)):
            hit = True
        elif ext == ".swift" and (name == "main.swift" or "@main" in text):
            hit = True
        elif ext == ".go" and re.search(r"func\s+main\s*\(", text) and re.search(r"package\s+main", text):
            hit = True
        if hit:
            entries.append(f)
        if len(entries) >= 20:
            break
    return entries


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def auto_block(files, syms, deps) -> str:
    lines = [f"## Files ({len(files)})"]
    for f in sorted(files)[:60]:
        lines.append(f"- `{f}`")
    if len(files) > 60:
        lines.append(f"- …and {len(files) - 60} more")
    lines.append("")
    lines.append(f"## Public symbols ({len(syms)})")
    for f, lineno, kind, name in syms[:60]:
        loc = f"{f}:{lineno}" if lineno else f
        lines.append(f"- `{kind} {name}` — {loc}")
    if len(syms) > 60:
        lines.append(f"- …and {len(syms) - 60} more")
    lines.append("")
    lines.append("## Dependencies (imports)")
    if deps:
        for d in deps[:30]:
            lines.append(f"- `{d}`")
        if len(deps) > 30:
            lines.append(f"- …and {len(deps) - 30} more")
    else:
        lines.append("- _none detected_")
    return "\n".join(lines)


def fresh_module_doc(key: str, inner: str) -> str:
    return (f"# Module: `{key}`\n\n"
            f"## Summary\n{SUMMARY_TODO}\n\n"
            f"{AUTO_START}\n{inner}\n{AUTO_END}\n")


def upsert_block(text: str, start: str, end: str, inner: str) -> str:
    block = f"{start}\n{inner}\n{end}"
    if start in text and end in text:
        i = text.index(start)
        j = text.index(end) + len(end)
        return text[:i] + block + text[j:]
    sep = "" if text == "" or text.endswith("\n") else "\n"
    return text + sep + "\n" + block + "\n"


ROW_RE = re.compile(r"^\|\s*`(?P<key>[^`]+)`\s*\|[^|]*\|[^|]*\|\s*(?P<one>.*?)\s*\|\s*$")


def parse_oneliners(text: str):
    out = {}
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            out[m.group("key")] = m.group("one")
    return out


def module_index(modinfo, depth: int, oneliners) -> str:
    lines = [f"## Modules ({len(modinfo)}) — grouping depth {depth}", "",
             "| Module | Files | Doc | One-liner |", "|---|---|---|---|"]
    for key in sorted(modinfo):
        info = modinfo[key]
        one = oneliners.get(key) or ONELINER_TODO
        lines.append(f"| `{key}` | {info['nfiles']} | [doc]({info['doc']}) | {one} |")
    return "\n".join(lines)


def fresh_arch(project: str, modblock: str, entries, builds) -> str:
    entry_lines = "\n".join(f"- `{e}`" for e in entries) if entries else "_TODO: list the main entry points._"
    build_lines = "\n".join(f"- **{label}**: `{cmd}`" for label, cmd in builds) if builds else "_TODO: how to build & test._"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (f"# {project} — Project Map\n\n"
            f"> Auto-generated by the `project-map` skill ({date}). Edit the prose sections\n"
            f"> freely; the module index between the markers is regenerated by `build`/`update`.\n\n"
            f"## Overview\n_TODO: 2–4 sentences — what this project is and its high-level architecture._\n\n"
            f"## Entry points\n{entry_lines}\n\n"
            f"## Build / test\n{build_lines}\n\n"
            f"## Conventions\n_TODO: conventions, patterns, and gotchas a fresh agent should know._\n\n"
            f"{MOD_START}\n{modblock}\n{MOD_END}\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    args = sys.argv[1:]
    mode = "build"
    path = "."
    for a in args:
        if a in ("build", "update", "status"):
            mode = a
        else:
            path = a

    root = Path(path).resolve()
    if not root.is_dir():
        die(f"not a directory: {root}")
    depth = max(1, int(os.environ.get("PROJECTMAP_DEPTH", "2")))

    files, used_git = list_source_files(root)
    if not files:
        print(f"[project-map] no source files found under {root} "
              f"(looked for {', '.join(sorted(EXTS))}).")
        return

    # group into modules
    groups = {}
    for f in files:
        groups.setdefault(module_key(f, depth), []).append(f)

    out = root / OUT_DIR
    manifest_path = out / "manifest.json"
    prev = {}
    if manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text()).get("modules", {})
        except Exception:
            prev = {}

    # current hashes + status
    cur_hash = {key: hash_module(root, fs) for key, fs in groups.items()}
    status = {}
    for key in groups:
        if key not in prev:
            status[key] = "new"
        elif prev[key].get("hash") != cur_hash[key]:
            status[key] = "changed"
        else:
            status[key] = "unchanged"
    removed = [k for k in prev if k not in groups]

    # ---- status: read-only drift report -------------------------------- #
    if mode == "status":
        if not prev:
            print("[project-map] no manifest yet — run `/project-map build` first.")
            return
        new = [k for k in status if status[k] == "new"]
        chg = [k for k in status if status[k] == "changed"]
        print(f"[project-map] status for {root.name}: {len(groups)} modules, "
              f"{len(files)} files. new={len(new)} changed={len(chg)} removed={len(removed)}.")
        for k in sorted(new):
            print(f"  + {k} (new)")
        for k in sorted(chg):
            print(f"  ~ {k} (changed)")
        for k in sorted(removed):
            print(f"  - {k} (removed)")
        if new or chg or removed:
            print("Run `/project-map update` to refresh.")
        else:
            print("Map is up to date.")
        return

    # ---- build / update: write everything ------------------------------ #
    check_ctags()
    modules_dir = out / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    options, used_parsers = optlib_options(files)
    run_ctags(root, files, out / "tags", options)
    by_file = parse_tags(out / "tags")
    warn_empty_languages(root, files, by_file)

    modinfo = {}
    needs_summary = []
    for key, fs in sorted(groups.items()):
        safe = safe_name(key)
        doc_rel = f"modules/{safe}.md"
        docfile = modules_dir / f"{safe}.md"
        syms = module_symbols(by_file, fs)
        deps = gather_deps(root, fs)
        inner = auto_block(fs, syms, deps)
        refresh = (mode == "build") or status[key] in ("new", "changed")

        if docfile.exists():
            if refresh:
                docfile.write_text(upsert_block(docfile.read_text(), AUTO_START, AUTO_END, inner))
            summary_todo = SUMMARY_TODO in docfile.read_text()
        else:
            docfile.write_text(fresh_module_doc(key, inner))
            summary_todo = True

        modinfo[key] = {"nfiles": len(fs), "doc": doc_rel, "hash": cur_hash[key]}
        if summary_todo or status[key] == "changed":
            needs_summary.append((key, "unsummarized" if summary_todo else "changed", doc_rel))

    # remove docs for modules that no longer exist
    for key in removed:
        old = modules_dir / f"{safe_name(key)}.md"
        if old.exists():
            old.unlink()

    # ARCHITECTURE.md — refresh module index, preserve prose
    archfile = out / "ARCHITECTURE.md"
    if archfile.exists():
        text = archfile.read_text()
        modblock = module_index(modinfo, depth, parse_oneliners(text))
        archfile.write_text(upsert_block(text, MOD_START, MOD_END, modblock))
    else:
        modblock = module_index(modinfo, depth, {})
        archfile.write_text(fresh_arch(root.name, modblock,
                                       detect_entries(root, files), detect_build(root)))

    # manifest
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "depth": depth,
        "source": "git" if used_git else "walk",
        "files": len(files),
        "modules": {k: {"hash": v["hash"], "files": v["nfiles"], "doc": v["doc"]}
                    for k, v in modinfo.items()},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    # report for the agent
    print(f"[project-map] {mode}: indexed {len(files)} files into {len(groups)} modules "
          f"(depth {depth}). tags -> {OUT_DIR}/tags")
    if used_parsers:
        print(f"  bundled parser used for: {', '.join(sorted(used_parsers))} "
              f"(stock ctags has no native parser).")
    for key in removed:
        print(f"  removed module: {key}")
    if needs_summary:
        print("Modules needing a summary — open each doc, read the listed files, then write "
              "its `## Summary` and the matching one-liner in ARCHITECTURE.md:")
        for key, why, doc_rel in needs_summary:
            print(f"  - {key} ({why}) -> {OUT_DIR}/{doc_rel}")
    else:
        print("All modules already summarized; map is up to date.")
    print(f"Also fill any remaining TODOs in {OUT_DIR}/ARCHITECTURE.md (Overview / Entry points / Conventions).")


if __name__ == "__main__":
    main()
