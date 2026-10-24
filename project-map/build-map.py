#!/usr/bin/env python3
"""
project-map — build/update a compact, on-demand project map for coding agents.

This is the deterministic "mechanical" layer of the project-map skill. It runs
universal-ctags to index symbols, groups source files into modules, resolves
imports into an internal module dependency graph, and emits skeleton Markdown
under <project>/.projectmap/ for an agent to fill with short semantic
summaries. It is meant to be re-run incrementally (`update`) so only changed
modules need re-summarizing.

The script NEVER writes the human/agent-authored prose: it only (re)generates
the blocks between the `projectmap:*` markers (in .projectmap/ and in the
project's CLAUDE.md / AGENTS.md / GEMINI.md), plus `tags` and `manifest.json`.
Everything between/outside those markers (the `## Summary` sections, the prose
in ARCHITECTURE.md, the rest of CLAUDE.md) is preserved across runs.

Usage:
    build-map.py [build|update|status|affected] [PROJECT_PATH] [options]

    build     full reindex; refresh every generated block (summaries kept)
    update    same deterministic work as build (a block is only rewritten when
              its content changed); reports the modules needing a summary
    status    read-only drift report (no ctags, no writes)
    affected  read-only: which modules and test files import, directly or
              transitively, the changed files. By default the changed files
              are the uncommitted changes vs HEAD.
                --base REF   changes vs REF instead (e.g. `--base main`)
                --stdin      read changed paths from stdin instead
                --depth N    how many import hops to follow (default 5)
                --tests      print only the affected test files, one per line

Env:
    PROJECTMAP_DEPTH   module grouping depth (default 2)

Config (optional, user-owned, never written by this script):
    .projectmap/config.json   {"exclude": ["static/", "**/vendor/**"]}
    gitignore-style patterns matched against project-relative paths, for
    committed vendored/generated trees .gitignore can't drop. No `!` negation
    and no [...] classes. Files over 1 MB are always skipped.

For languages stock Universal Ctags can't parse (e.g. Swift), a bundled regex
optlib under parsers/ is auto-loaded — but only when ctags reports no native
parser, so it stays conflict-free if upstream adds one later.

Requires: python3 (stdlib only) and universal-ctags on PATH (not for status
or affected).
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = ".projectmap"
CONFIG_FILE = "config.json"

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
    ".go", ".java", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".kt", ".rb", ".cs", ".m", ".mm",
}

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "target", "build", "dist", "out",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".build", ".swiftpm", "Pods", "DerivedData", "vendor", "third_party", OUT_DIR,
}

# Build manifests read (not indexed) to resolve imports across packages.
MANIFEST_NAMES = {"go.mod", "Cargo.toml", "package.json"}

# Bigger files are generated bundles, minified JS or vendored blobs.
MAX_FILE_BYTES = 1024 * 1024

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
POINTER_START = "<!-- projectmap:pointer:start (generated — do not edit by hand) -->"
POINTER_END = "<!-- projectmap:pointer:end -->"
SUMMARY_TODO = "_TODO: summarize this module (~3 sentences) — what it does, why it exists, its role._"
ONELINER_TODO = "_TODO_"

# The always-on instruction block written into the project's agent files. The
# main agent reads it every turn, so it stays short: where to look, and the
# habits that make the map pay off (don't re-derive it, don't delegate the
# exploration it replaces).
POINTER_BODY = """\
## Project map
This repo has a committed `.projectmap/` index. Read it before grepping, listing or opening files to find your way around:
- `.projectmap/ARCHITECTURE.md`: modules, how they depend on each other, entry points, build/test, conventions.
- `.projectmap/modules/<module>.md`: one module's summary, files, symbols, and which modules it uses / is used by. Open only the ones you work in.
- Locate a definition with `grep -w '^Name' .projectmap/tags` (name, file, line, kind) instead of searching the tree.
- Don't re-derive what the map already lists: no `ls`/`find`/`grep` sweeps for layout or symbol locations, and don't hand that exploration to a file-reading subagent. Open source files once you know which ones you need.
- Before changing a module's interface, check its "Used by"; `python3 ~/.claude/skills/project-map/build-map.py affected` lists the modules and tests your uncommitted changes reach.
- The map can lag the code. Modules that `python3 ~/.claude/skills/project-map/build-map.py status` lists as changed (Claude Code reports them at session start; elsewhere, run it once before relying on the map) may have stale docs: read their source instead. Every other module's doc is current.
- `.projectmap/` is generated except the module summaries: don't hand-edit it; refresh with `/project-map update`."""

# The unfenced block earlier versions told the agent to paste into CLAUDE.md;
# replaced in place by the fenced one so the two don't stack.
LEGACY_POINTER_RE = re.compile(r"^## Project map[ \t]*\n(?:.*\n){0,8}?.*/project-map update.*(?:\n|$)", re.M)

C_EXTS = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".C", ".H", ".m", ".mm"}
C_IMPL_EXTS = {".c", ".cc", ".cpp", ".cxx", ".C", ".m", ".mm"}
C_HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".H")
PY_EXTS = {".py", ".pyi"}
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
JVM_EXTS = {".java", ".kt"}

# Generated code: path conventions of common generators (protobuf/gRPC, mocks,
# codegen suffixes, minified bundles), plus the header banners they write.
# Generated files stay in `tags`, but their symbols are left out of a module's
# "Public symbols" so they can't take the slots of hand-written code.
GENERATED_PATH_RE = re.compile("|".join([
    r"\.pb\.go$", r"_mocks?\.go$", r"(?:^|/)mock_[^/]+\.go$",
    r"\.(?:generated|gen)\.[jt]sx?$", r"[._]pb\.[jt]s$", r"_grpc_pb\.[jt]s$", r"[.-]min\.[mc]?js$",
    r"_pb2(?:_grpc)?\.pyi?$",
    r"\.pb\.(?:cc|h)$",
    r"\.g\.cs$", r"Grpc\.(?:cs|java)$", r"OuterClass\.java$",
    r"\.pb\.swift$", r"\.generated\.(?:rs|swift)$",
]))
GENERATED_BANNER_RES = [re.compile(p, re.I) for p in (
    r"\bcode generated\b.{0,200}?\bdo not edit\b",
    r"\b(?:automatically |auto[- ]?)?generated (?:by|from|with)\b.{0,200}?\bdo not (?:edit|modify|change)\b",
    r"(?:^|[^\w@])@generated\b",
    r"<auto-?generated\s*/?>",
    r"\b(?:automatically generated|auto[- ]?generated|autogenerated) by\b",
    r"\bthis (?:file|class|code|module) (?:is|was) (?:auto[- ]?)?generated\b",
    r"\bdo not (?:edit|modify)\b.{0,120}?\b(?:auto[- ]?generated|generated file|generated code)\b",
)]
# A banner only counts on a comment line, so a generator's own source (which
# holds the banner as a string constant) isn't flagged.
COMMENT_LEADER_RE = re.compile(r"^\s*(?://|/\*|\*|#|--|<!--|\"\"\"|''')")
BLOCK_COMMENTS = (("/*", "*/"), ('"""', '"""'), ("'''", "'''"), ("<!--", "-->"))
HEADER_SCAN_CHARS = 8192
HEADER_SCAN_LINES = 60

# Test files, for `affected --tests`.
TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec|Tests)/"
    r"|(?:^|/)test_[^/]+\.py$|_test\.(?:py|go|rb)$|\.(?:test|spec)\.[jt]sx?$|_spec\.rb$"
    r"|Tests?\.(?:swift|java|kt)$")

C_INCLUDE_RE = re.compile(r'#\s*(?:include|import)\s*([<"])([^>"]+)[>"]')
PY_FROM_RE = re.compile(r"from\s+(\.*)([A-Za-z0-9_.]*)\s+import\s+(.+)")
PY_IMPORT_RE = re.compile(r"import\s+(.+)")
JS_SPEC_RE = re.compile(r"""(?:\bfrom|\bimport(?:\s*\()?|\brequire\s*\()\s*['"]([^'"\s,]+)['"]""")
GO_SINGLE_RE = re.compile(r'import\s+(?:[\w.]+\s+)?"([^"]+)"')
GO_SPEC_RE = re.compile(r'(?:[\w.]+\s+)?"([^"]+)"')
RS_USE_RE = re.compile(r"(?:pub(?:\([^)]*\))?\s+)?use\s+(?:::)?([A-Za-z0-9_]+(?:::[A-Za-z0-9_]+)*)")
RS_MOD_RE = re.compile(r"(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z0-9_]+)\s*;")
RS_EXTERN_RE = re.compile(r"extern\s+crate\s+([A-Za-z0-9_]+)")
SWIFT_IMPORT_RE = re.compile(
    r"(?:@testable\s+)?import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?([A-Za-z0-9_]+)")
JVM_IMPORT_RE = re.compile(r"import\s+(?:static\s+)?([A-Za-z_][\w.]*)(\*)?")
RB_REQUIRE_RE = re.compile(r"""require(_relative)?\s*\(?\s*['"]([^'"]+)['"]""")


def die(msg: str) -> None:
    sys.stderr.write(f"[project-map] error: {msg}\n")
    sys.exit(1)


def warn(msg: str) -> None:
    sys.stderr.write(f"[project-map] warning: {msg}\n")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(root: Path) -> dict:
    path = root / OUT_DIR / CONFIG_FILE
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text())
    except Exception as e:
        warn(f"ignoring unreadable {OUT_DIR}/{CONFIG_FILE}: {e}")
        return {}
    if not isinstance(cfg, dict):
        warn(f"ignoring {OUT_DIR}/{CONFIG_FILE}: expected a JSON object")
        return {}
    return cfg


def glob_regex(pat: str) -> str:
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out.append("/.*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return "".join(out)


def compile_excludes(patterns):
    """gitignore-style patterns -> regexes matching a file or anything under a dir."""
    if not isinstance(patterns, list):
        warn(f"`exclude` in {OUT_DIR}/{CONFIG_FILE} must be a list of patterns; ignoring it")
        return []
    out = []
    for raw in patterns:
        pat = raw.strip() if isinstance(raw, str) else ""
        if not pat or pat.startswith("#"):
            continue
        if pat.startswith("!"):
            warn(f"`!` negation isn't supported in `exclude`; skipping {pat!r}")
            continue
        dir_only = pat.endswith("/")
        body = pat.strip("/")
        if not body:
            continue
        # As in .gitignore, a pattern with no inner slash matches at any depth.
        anchored = pat.startswith("/") or "/" in body
        prefix = "" if anchored else "(?:.*/)?"
        tail = "/.+" if dir_only else "(?:/.+)?"
        out.append(re.compile(f"^{prefix}{glob_regex(body)}{tail}$"))
    return out


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
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and (d == ".claude" or not d.startswith("."))]
        for fn in filenames:
            rel = (Path(dirpath) / fn).relative_to(root)
            found.append(str(rel).replace(os.sep, "/"))
    return found


def list_files(root: Path, excludes):
    """Source files to index, plus the build manifests import resolution reads.

    Returns (sources, manifests, used_git, oversize, n_excluded).
    """
    raw = git_files(root)
    used_git = raw is not None
    if raw is None:
        raw = walk_files(root)
    sources, manifests, oversize, n_excluded = [], [], [], 0
    for f in sorted(set(raw)):
        if not f or any(seg in EXCLUDE_DIRS for seg in f.split("/")):
            continue
        is_manifest = posixpath.basename(f) in MANIFEST_NAMES
        if not is_manifest and Path(f).suffix not in EXTS:
            continue
        if any(r.match(f) for r in excludes):
            n_excluded += not is_manifest
            continue
        p = root / f
        if not p.is_file():
            continue
        if is_manifest:
            manifests.append(f)
        elif p.stat().st_size > MAX_FILE_BYTES:
            oversize.append(f)
        else:
            sources.append(f)
    return sources, manifests, used_git, oversize, n_excluded


def module_key(rel: str, depth: int) -> str:
    parts = [p for p in Path(rel).parent.parts if p != "."]
    if not parts:
        return "(root)"
    return "/".join(parts[:depth])


def safe_name(key: str) -> str:
    if key == "(root)":
        return "_root"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key.replace("/", "__"))


def is_generated(root: Path, rel: str) -> bool:
    if GENERATED_PATH_RE.search(rel):
        return True
    try:
        with (root / rel).open("rb") as fh:
            head = fh.read(HEADER_SCAN_CHARS).decode("utf-8", errors="replace")
    except OSError:
        return False
    lines = head.splitlines()
    # Minified bundles: nobody writes a 1000-character line by hand.
    if Path(rel).suffix in {".js", ".mjs", ".cjs"} and any(len(l) > 1000 for l in lines[:5]):
        return True
    if "generat" not in head.lower():
        return False
    close = None  # closer of the block comment / docstring we're inside, if any
    for l in lines[:HEADER_SCAN_LINES]:
        if (close or COMMENT_LEADER_RE.match(l)) and any(p.search(l) for p in GENERATED_BANNER_RES):
            return True
        if close:
            close = None if close in l else close
            continue
        for opener, closer in BLOCK_COMMENTS:
            at = l.find(opener)
            if at >= 0:
                close = None if l.find(closer, at + len(opener)) >= 0 else closer
                break
    return False


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
        warn("ctags does not look like Universal Ctags; symbol extraction may be degraded.")


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
            warn(f"bundled {lang} parser missing at {opt}; {lang} symbols will be empty.")
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
        warn("0 symbols extracted for " + ", ".join(blind) +
             " — ctags likely has no parser for these. The map's `Public symbols` "
             "will be empty for those modules.")


def run_ctags(root: Path, files, tags_path: Path, extra_options=None) -> None:
    cmd = ["ctags", *(extra_options or []),
           # ctags doesn't map ES-module extensions to JavaScript by default, so
           # `.mjs`/`.cjs` would index to zero symbols. Extend the language map.
           "--map-JavaScript=+.mjs", "--map-JavaScript=+.cjs",
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
# Imports -> internal dependency graph
# --------------------------------------------------------------------------- #
def py_names(s: str):
    """Imported names from the tail of an import statement (`a, b as c`)."""
    s = s.split("#", 1)[0].replace("(", " ").replace(")", " ").replace("\\", " ")
    out = []
    for part in s.split(","):
        tok = part.split()
        if tok and tok[0] != "*":
            out.append(tok[0])
    return out


def parse_imports(text: str, ext: str):
    """Raw import records of one source file, line-based and best-effort."""
    out = []
    in_doc = False       # inside a Python triple-quoted string
    in_go_block = False  # inside a Go `import ( ... )` block
    for line in text.splitlines():
        s = line.strip()
        if ext in C_EXTS:
            m = C_INCLUDE_RE.match(s)
            if m:
                out.append(("c", m.group(1) == '"', m.group(2)))
        elif ext in PY_EXTS:
            if s.count('"""') % 2 or s.count("'''") % 2:
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            m = PY_FROM_RE.match(s)
            if m:
                out.append(("py", len(m.group(1)), m.group(2), py_names(m.group(3))))
                continue
            m = PY_IMPORT_RE.match(s)
            if m:
                out.extend(("py", 0, name, []) for name in py_names(m.group(1)))
        elif ext in JS_EXTS:
            out.extend(("js", spec) for spec in JS_SPEC_RE.findall(s))
        elif ext == ".go":
            if in_go_block:
                if s.startswith(")"):
                    in_go_block = False
                    continue
                m = GO_SPEC_RE.match(s)
                if m:
                    out.append(("go", m.group(1)))
            elif re.match(r"import\s*\(", s):
                in_go_block = ")" not in s
                out.extend(("go", p) for p in re.findall(r'"([^"]+)"', s))
            else:
                m = GO_SINGLE_RE.match(s)
                if m:
                    out.append(("go", m.group(1)))
        elif ext == ".rs":
            m = RS_USE_RE.match(s) or RS_EXTERN_RE.match(s)
            if m:
                out.append(("rs-use", m.group(1).split("::")))
                continue
            m = RS_MOD_RE.match(s)
            if m:
                out.append(("rs-mod", m.group(1)))
        elif ext == ".swift":
            m = SWIFT_IMPORT_RE.match(s)
            if m:
                out.append(("swift", m.group(1)))
        elif ext in JVM_EXTS:
            m = JVM_IMPORT_RE.match(s)
            if m:
                out.append(("jvm", m.group(1).rstrip("."), bool(m.group(2))))
        elif ext == ".rb":
            m = RB_REQUIRE_RE.match(s)
            if m:
                out.append(("rb", bool(m.group(1)), m.group(2)))
    return out


def closest(importer: str, cands):
    """The candidate sharing the longest directory prefix with the importer."""
    ip = importer.split("/")

    def score(c):
        cp = c.split("/")
        n = 0
        for a, b in zip(ip, cp):
            if a != b:
                break
            n += 1
        return (-n, len(cp), c)
    return min(cands, key=score)


class Resolver:
    """Best-effort mapping from an import record to the project files it names.

    `resolve()` returns (targets, external): `targets` are project files the
    import resolves to; `external` names an unresolved non-relative import (a
    third-party or system dependency), or None. Relative imports that don't
    resolve are dropped rather than reported as external. C# `using` and
    anything dynamic (reflection, runtime loading) aren't traced.
    """

    def __init__(self, root: Path, files, manifests):
        self.files = set(files)
        self.by_dir = {}      # dir -> files directly in it
        self.suffix = {}      # every path suffix on a "/" boundary -> files
        self.dir_suffix = {}  # same, for directories
        self.py = {}          # dotted-name suffix -> python files
        self.swift_files = []
        self.swift_targets = {}  # SwiftPM target name -> its Sources/Tests dirs
        self.swift_dirs = {}     # any dir basename -> dirs holding swift files
        self._under = {}
        for f in files:
            self.by_dir.setdefault(posixpath.dirname(f), []).append(f)
            parts = f.split("/")
            for i in range(len(parts)):
                self.suffix.setdefault("/".join(parts[i:]), []).append(f)
            ext = posixpath.splitext(f)[1]
            if ext in PY_EXTS:
                mod = f.rsplit(".", 1)[0].split("/")
                if mod[-1] == "__init__":
                    mod = mod[:-1]
                for i in range(len(mod)):
                    self.py.setdefault(".".join(mod[i:]), []).append(f)
            elif ext == ".swift":
                self.swift_files.append(f)
                for i, p in enumerate(parts[:-2]):
                    if p in ("Sources", "Tests"):
                        self.swift_targets.setdefault(parts[i + 1], set()).add("/".join(parts[:i + 2]))
                        break
                for i in range(1, len(parts)):
                    self.swift_dirs.setdefault(parts[i - 1], set()).add("/".join(parts[:i]))
        for d in self.by_dir:
            parts = d.split("/") if d else []
            for i in range(len(parts)):
                self.dir_suffix.setdefault("/".join(parts[i:]), []).append(d)

        self.go_mods = []      # (module path, dir), longest first
        self.cargo_dirs = set()
        self.crates = {}       # crate name (as written in `use`) -> crate dir
        self.js_pkgs = {}      # package name -> (dir, entry candidates)
        for m in manifests:
            d, name = posixpath.split(m)
            try:
                text = (root / m).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if name == "go.mod":
                mm = re.search(r"^module\s+\"?([^\s\"]+)", text, re.M)
                if mm:
                    self.go_mods.append((mm.group(1), d))
            elif name == "Cargo.toml":
                self.cargo_dirs.add(d)
                section, names = None, {}
                for line in text.splitlines():
                    s = line.strip()
                    sm = re.match(r"\[([^\]]+)\]", s)
                    if sm:
                        section = sm.group(1).strip()
                        continue
                    nm = re.match(r'name\s*=\s*"([^"]+)"', s)
                    if nm and section in ("package", "lib"):
                        names[section] = nm.group(1)
                crate = names.get("lib") or names.get("package")
                if crate:
                    self.crates[crate.replace("-", "_")] = d
            elif name == "package.json":
                try:
                    pkg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(pkg, dict) and isinstance(pkg.get("name"), str):
                    entries = [posixpath.join(d, pkg[k]) for k in ("source", "module", "main", "types")
                               if isinstance(pkg.get(k), str)]
                    entries += [posixpath.join(d, "src", "index"), posixpath.join(d, "index")]
                    self.js_pkgs[pkg["name"]] = (d, entries)
        self.go_mods.sort(key=lambda x: -len(x[0]))

    def resolve(self, importer: str, rec):
        kind = rec[0]
        if kind == "c":
            return self._c(importer, rec[1], rec[2])
        if kind == "py":
            return self._py(importer, rec[1], rec[2], rec[3])
        if kind == "js":
            return self._js(importer, rec[1])
        if kind == "go":
            return self._go(rec[1])
        if kind == "rs-use":
            return self._rs_use(importer, rec[1])
        if kind == "rs-mod":
            return self._rs_mod(importer, rec[1])
        if kind == "swift":
            return self._swift(rec[1])
        if kind == "jvm":
            return self._jvm(importer, rec[1], rec[2])
        if kind == "rb":
            return self._rb(importer, rec[1], rec[2])
        return [], None

    # -- C / C++ / Objective-C ------------------------------------------------
    def _c(self, importer, quoted, spec):
        if quoted:
            p = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
            if p in self.files:
                return [p], None
        key = posixpath.normpath(spec)
        while key.startswith("../"):
            key = key[3:]
        cands = self.suffix.get(key)
        if cands:
            return [closest(importer, cands)], None
        return [], spec

    # -- Python ---------------------------------------------------------------
    def _py_file(self, p):
        for c in (p + ".py", p + ".pyi", posixpath.join(p, "__init__.py"), posixpath.join(p, "__init__.pyi")):
            if c in self.files:
                return c
        return None

    def _is_py_pkg(self, d):
        return any(posixpath.join(d, n) in self.files for n in ("__init__.py", "__init__.pyi"))

    def _py_top_ok(self, importer, cand):
        # A bare `import x` only means a project file when x is a top-level
        # package, sits at a source root, or is the importer's sibling —
        # otherwise `utils/json.py` would capture every `import json`.
        d = posixpath.dirname(cand)
        if posixpath.basename(cand).startswith("__init__."):
            return not self._is_py_pkg(posixpath.dirname(d))
        return d in ("", "src", "lib", "python") or d == posixpath.dirname(importer)

    def _py_lookup(self, importer, dotted):
        comps = dotted.split(".")
        for k in range(len(comps), 0, -1):
            cands = self.py.get(".".join(comps[:k]))
            if cands and k == 1:
                cands = [c for c in cands if self._py_top_ok(importer, c)]
            if cands:
                return [closest(importer, cands)]
        return []

    def _py(self, importer, dots, mod, names):
        if dots:
            base = posixpath.dirname(importer)
            for _ in range(dots - 1):
                if not base:
                    return [], None
                base = posixpath.dirname(base)
            mod_path = posixpath.join(base, *mod.split(".")) if mod else base
            hits = [h for n in names if (h := self._py_file(posixpath.join(mod_path, n)))]
            if not hits:
                h = self._py_file(mod_path)  # names are symbols of the module itself
                hits = [h] if h else []
            return hits, None
        if not mod:
            return [], None
        hits = set()
        for n in names:
            hits.update(self._py_lookup(importer, f"{mod}.{n}"))
        if not hits:
            hits.update(self._py_lookup(importer, mod))
        top = mod.split(".")[0]
        return sorted(hits), (None if hits or top == "__future__" else top)

    # -- JavaScript / TypeScript ----------------------------------------------
    def _js_file(self, p):
        p = posixpath.normpath(p)
        if p == ".":
            p = ""
        if p == ".." or p.startswith("../"):
            return None
        if p in self.files:
            return p
        stem, ext = posixpath.splitext(p)
        cands = [p + e for e in JS_EXTS] + [posixpath.join(p, "index" + e) for e in JS_EXTS]
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            cands += [stem + ".ts", stem + ".tsx"]  # TS ESM imports name the emitted .js
        return next((c for c in cands if c in self.files), None)

    def _js(self, importer, spec):
        if spec in (".", "..") or spec.startswith(("./", "../")):
            hit = self._js_file(posixpath.join(posixpath.dirname(importer), spec))
            return ([hit] if hit else []), None
        if spec.startswith(("@/", "~/")):  # the common src/ path alias
            hit = self._js_file("src/" + spec[2:]) or self._js_file(spec[2:])
            return ([hit] if hit else []), None
        if spec.startswith("/"):
            return [], None
        name = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        if name in self.js_pkgs:  # a package of this workspace
            d, entries = self.js_pkgs[name]
            sub = spec[len(name):].lstrip("/")
            cands = [posixpath.join(d, sub), posixpath.join(d, "src", sub)] if sub else entries
            hit = next((h for c in cands if (h := self._js_file(c))), None)
            return ([hit] if hit else []), None
        return [], name

    # -- Go -------------------------------------------------------------------
    def _go(self, path):
        for modpath, d in self.go_mods:
            if path == modpath or path.startswith(modpath + "/"):
                rel = path[len(modpath):].lstrip("/")
                pdir = posixpath.join(d, rel) if rel else d
                return [f for f in self.by_dir.get(pdir, [])
                        if f.endswith(".go") and not f.endswith("_test.go")], None
        return [], path

    # -- Rust -----------------------------------------------------------------
    def _rs_crate_dir(self, f):
        d = posixpath.dirname(f)
        while True:
            if d in self.cargo_dirs:
                return d
            if not d:
                return None
            d = posixpath.dirname(d)

    @staticmethod
    def _rs_self_dir(f):
        """Directory holding the child modules of the module defined by `f`."""
        d, name = posixpath.split(f)
        return d if name in ("mod.rs", "lib.rs", "main.rs") else posixpath.join(d, name[:-3])

    @staticmethod
    def _rs_parent_dir(f):
        d, name = posixpath.split(f)
        return posixpath.dirname(d) if name == "mod.rs" else d

    def _rs_file(self, base, comps):
        """The longest module path under `base` that exists, else the module's own file."""
        for k in range(len(comps), 0, -1):
            p = posixpath.join(base, *comps[:k])
            for c in (p + ".rs", posixpath.join(p, "mod.rs")):
                if c in self.files:
                    return c
        for c in [posixpath.join(base, n) for n in ("mod.rs", "lib.rs", "main.rs")] + [base + ".rs"]:
            if c in self.files:
                return c
        return None

    def _rs_use(self, importer, comps):
        head, rest = comps[0], comps[1:]
        if head == "crate":
            cd = self._rs_crate_dir(importer)
            base = posixpath.join(cd, "src") if cd is not None else None
        elif head == "self":
            base = self._rs_self_dir(importer)
        elif head == "super":
            base = self._rs_parent_dir(importer)
            while rest and rest[0] == "super":
                base, rest = posixpath.dirname(base), rest[1:]
        elif head in self.crates:  # another crate of this workspace
            base = posixpath.join(self.crates[head], "src")
        else:
            return [], head
        hit = self._rs_file(base, rest) if base is not None else None
        return ([hit] if hit else []), None

    def _rs_mod(self, importer, name):
        base = self._rs_self_dir(importer)
        for c in (posixpath.join(base, name + ".rs"), posixpath.join(base, name, "mod.rs")):
            if c in self.files:
                return [c], None
        return [], None

    # -- Swift ----------------------------------------------------------------
    def _swift(self, name):
        dirs = self.swift_targets.get(name)
        if not dirs and name in self.swift_dirs:
            dirs = [min(self.swift_dirs[name], key=len)]
        if not dirs:
            return [], name
        hits = []
        for d in dirs:
            if d not in self._under:
                self._under[d] = [f for f in self.swift_files if f.startswith(d + "/")]
            hits += self._under[d]
        return hits, None

    # -- Java / Kotlin --------------------------------------------------------
    def _jvm(self, importer, spec, wildcard):
        comps = spec.split(".")
        if wildcard:
            dirs = self.dir_suffix.get("/".join(comps), [])
            hits = [f for d in dirs for f in self.by_dir[d] if Path(f).suffix in JVM_EXTS]
            if hits:
                return hits, None
        else:
            for k in range(len(comps), 1, -1):
                key = "/".join(comps[:k])
                cands = self.suffix.get(key + ".java", []) + self.suffix.get(key + ".kt", [])
                if cands:
                    return [closest(importer, cands)], None
        pkg = []
        for c in comps:  # report the package, not every class imported from it
            if c[:1].isupper():
                break
            pkg.append(c)
        return [], ".".join(pkg) or spec

    # -- Ruby -----------------------------------------------------------------
    def _rb(self, importer, relative, spec):
        key = spec if spec.endswith(".rb") else spec + ".rb"
        if relative:
            p = posixpath.normpath(posixpath.join(posixpath.dirname(importer), key))
            return ([p] if p in self.files else []), None
        cands = self.suffix.get("lib/" + key)
        if cands:
            return [closest(importer, cands)], None
        return [], spec.split("/")[0]


def scan_imports(root: Path, files, resolver: Resolver):
    """Resolve every file's imports: (file -> internal targets, file -> external names)."""
    deps, externals = {}, {}
    for f in files:
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets, ext_names = set(), set()
        for rec in parse_imports(text, Path(f).suffix):
            hits, external = resolver.resolve(f, rec)
            targets.update(hits)
            if external:
                ext_names.add(external)
        targets.discard(f)
        deps[f], externals[f] = targets, ext_names
    return deps, externals


def module_graph(file_deps, mod_of):
    """Module-level edges: (module -> modules it imports, module -> modules importing it)."""
    uses, used_by = {}, {}
    for f, targets in file_deps.items():
        m = mod_of[f]
        for t in targets:
            if mod_of[t] != m:
                uses.setdefault(m, set()).add(mod_of[t])
                used_by.setdefault(mod_of[t], set()).add(m)
    return uses, used_by


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
def module_links(mods, cap=30):
    lines = [f"- [`{m}`]({safe_name(m)}.md)" for m in sorted(mods)[:cap]]
    if len(mods) > cap:
        lines.append(f"- …and {len(mods) - cap} more")
    return lines or ["- _none detected_"]


def auto_block(files, generated, syms, n_generated_syms, uses, used_by, externals) -> str:
    lines = [f"## Files ({len(files)})"]
    for f in sorted(files)[:60]:
        lines.append(f"- `{f}`" + (" _(generated)_" if f in generated else ""))
    if len(files) > 60:
        lines.append(f"- …and {len(files) - 60} more")
    lines.append("")
    lines.append(f"## Public symbols ({len(syms)})")
    for f, lineno, kind, name in syms[:60]:
        loc = f"{f}:{lineno}" if lineno else f
        lines.append(f"- `{kind} {name}` — {loc}")
    if len(syms) > 60:
        lines.append(f"- …and {len(syms) - 60} more")
    if n_generated_syms:
        lines.append(f"- …plus {n_generated_syms} in generated files (grep `{OUT_DIR}/tags`)")
    lines.append("")
    lines.append("## Depends on (internal modules)")
    lines += module_links(uses)
    lines.append("")
    lines.append("## Used by (internal modules)")
    lines += module_links(used_by)
    lines.append("")
    lines.append("## External imports")
    if externals:
        for d in externals[:30]:
            lines.append(f"- `{d}`")
        if len(externals) > 30:
            lines.append(f"- …and {len(externals) - 30} more")
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
    if not text:
        return block + "\n"
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + block + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


ROW_RE = re.compile(r"^\|\s*`(?P<key>[^`]+)`\s*\|[^|]*\|[^|]*\|\s*(?P<one>.*?)\s*\|\s*$")


def parse_oneliners(text: str):
    out = {}
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            out[m.group("key")] = m.group("one")
    return out


def module_index(modinfo, depth: int, oneliners, uses) -> str:
    lines = [f"## Modules ({len(modinfo)}) — grouping depth {depth}", "",
             "| Module | Files | Doc | One-liner |", "|---|---|---|---|"]
    for key in sorted(modinfo):
        info = modinfo[key]
        one = oneliners.get(key) or ONELINER_TODO
        lines.append(f"| `{key}` | {info['nfiles']} | [doc]({info['doc']}) | {one} |")
    edges = [(k, sorted(uses[k])) for k in sorted(modinfo) if uses.get(k)]
    if edges:
        lines += ["", "### Internal dependencies (module → modules it imports)"]
        for key, targets in edges:
            shown = ", ".join(f"`{t}`" for t in targets[:12])
            more = f", …+{len(targets) - 12}" if len(targets) > 12 else ""
            lines.append(f"- `{key}` → {shown}{more}")
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
# Agent instruction files
# --------------------------------------------------------------------------- #
def pointer_targets(root: Path):
    """Agent files that carry the pointer: Claude's (created if missing), plus
    AGENTS.md (Codex, opencode) and GEMINI.md (Gemini, Antigravity) when the
    project has them. A symlink is written once, through its target, and only
    if that target is inside the project."""
    claude = next((root / p for p in ("CLAUDE.md", ".claude/CLAUDE.md") if (root / p).exists()),
                  root / "CLAUDE.md")
    out, seen = [], set()
    for p in [claude, root / "AGENTS.md", root / "GEMINI.md"]:
        if p != claude and not p.exists():
            continue
        real = p.resolve()
        if real in seen:
            continue
        seen.add(real)
        try:
            real.relative_to(root)
        except ValueError:
            warn(f"{p.name} links outside the project ({real}); not adding the pointer there.")
            continue
        out.append(p)
    return out


def write_pointer(root: Path):
    """Upsert the pointer block; returns (files written, files already current)."""
    written, current = [], []
    for p in pointer_targets(root):
        text = p.read_text() if p.exists() else ""
        if POINTER_START not in text:
            m = LEGACY_POINTER_RE.search(text)
            if m and ".projectmap/" in m.group(0):
                text = text[:m.start()] + f"{POINTER_START}\n{POINTER_END}\n" + text[m.end():]
        new = upsert_block(text, POINTER_START, POINTER_END, POINTER_BODY)
        name = str(p.relative_to(root))
        (written if write_if_changed(p, new) else current).append(name)
        outside = new[:new.index(POINTER_START)] + new[new.index(POINTER_END):]
        if re.search(r"^## Project map\b", outside, re.M):
            warn(f"{name} still has a hand-written `## Project map` section besides the "
                 f"generated one; remove the old one.")
    return written, current


# --------------------------------------------------------------------------- #
# affected
# --------------------------------------------------------------------------- #
def git_out(root: Path, *args):
    try:
        return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root,
                              text=True, capture_output=True, check=True).stdout
    except Exception:
        return None


def changed_paths(root: Path, base, from_stdin):
    """(project-relative changed paths, description of where they came from)."""
    if from_stdin:
        raw = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
        prefix = (git_out(root, "rev-parse", "--show-prefix") or "").strip()
        out = []
        for p in raw:
            if os.path.isabs(p):
                p = os.path.relpath(p, root)
            p = p.replace(os.sep, "/")
            p = p[2:] if p.startswith("./") else p
            # `git diff --name-only` prints repo-root paths; make them project-relative.
            if prefix and p.startswith(prefix) and not (root / p).exists():
                p = p[len(prefix):]
            out.append(p)
        return out, "paths from stdin"
    ref = base or "HEAD"
    diff = git_out(root, "diff", "--name-only", "--relative", ref)
    if diff is None:
        die(f"`git diff {ref}` failed — not a git repo, or no such ref. Pipe paths with --stdin instead.")
    untracked = git_out(root, "ls-files", "--others", "--exclude-standard") or ""
    what = f"changes vs {base}" if base else "uncommitted changes vs HEAD"
    return diff.splitlines() + untracked.splitlines(), what


def run_affected(root, files, manifests, depth, opts):
    changed_raw, source = changed_paths(root, opts["base"], opts["stdin"])
    fileset = set(files)
    changed = sorted({p for p in changed_raw if p in fileset})
    ignored = len(set(changed_raw)) - len(changed)

    file_deps, _ = scan_imports(root, files, Resolver(root, files, manifests))
    rev = {}
    for f, targets in file_deps.items():
        for t in targets:
            rev.setdefault(t, set()).add(f)
    seeds = set(changed)
    # An implementation change reaches its callers through its own header: the
    # same-named one beside it or the same-named one it includes (include/x.h).
    for f in changed:
        stem, ext = posixpath.splitext(f)
        if ext in C_IMPL_EXTS:
            name = posixpath.basename(stem)
            seeds.update(stem + h for h in C_HEADER_EXTS if stem + h in fileset)
            seeds.update(h for h in file_deps.get(f, ())
                         if h.endswith(C_HEADER_EXTS) and posixpath.splitext(posixpath.basename(h))[0] == name)
    dist = {f: 0 for f in seeds}
    queue = deque(seeds)
    while queue:
        f = queue.popleft()
        if dist[f] >= opts["depth"]:
            continue
        for g in rev.get(f, ()):
            if g not in dist:
                dist[g] = dist[f] + 1
                queue.append(g)
    tests = sorted(f for f in dist if TEST_FILE_RE.search(f))

    if opts["tests"]:
        for t in tests:
            print(t)
        return
    mods = {}
    for f, d in dist.items():
        m = module_key(f, depth)
        n, best = mods.get(m, (0, d))
        mods[m] = (n + 1, min(best, d))
    print(f"[project-map] affected ({source}): {len(changed)} changed source file(s) -> "
          f"{len(dist)} file(s) in {len(mods)} module(s), up to {opts['depth']} import hops.")
    if ignored:
        print(f"  ignored {ignored} path(s) that aren't indexed source files (docs, deleted, excluded…)")
    if not changed:
        print("Nothing to trace.")
        return
    print("Modules (hops from a changed file; 0 = contains one):")
    for m, (n, d) in sorted(mods.items(), key=lambda x: (x[1][1], x[0])):
        print(f"  {d}  {m}  ({n} file{'s' if n != 1 else ''}) -> {OUT_DIR}/modules/{safe_name(m)}.md")
    print(f"Affected test files ({len(tests)}):")
    for t in tests[:50]:
        print(f"  - {t}")
    if len(tests) > 50:
        print(f"  - …and {len(tests) - 50} more (use --tests for the full list)")
    print("Import-based and best-effort: dynamic dispatch, reflection and runtime loading aren't traced.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv):
    mode, path = "build", "."
    opts = {"base": None, "stdin": False, "depth": 5, "tests": False}
    it = iter(argv)
    for a in it:
        if a in ("build", "update", "status", "affected"):
            mode = a
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif a == "--stdin":
            opts["stdin"] = True
        elif a == "--tests":
            opts["tests"] = True
        elif a in ("--base", "--depth") or a.startswith(("--base=", "--depth=")):
            key, _, val = a[2:].partition("=")
            val = val or next(it, "")
            if not val:
                die(f"--{key} needs a value")
            if key == "depth":
                if not val.isdigit():
                    die(f"--depth needs a number, got {val!r}")
                val = int(val)
            opts[key] = val
        elif a.startswith("--"):
            die(f"unknown option {a}")
        else:
            path = a
    return mode, path, opts


def main() -> None:
    mode, path, opts = parse_args(sys.argv[1:])

    root = Path(path).resolve()
    if not root.is_dir():
        die(f"not a directory: {root}")
    depth = max(1, int(os.environ.get("PROJECTMAP_DEPTH", "2")))

    excludes = compile_excludes(load_config(root).get("exclude", []))
    files, manifests, used_git, oversize, n_excluded = list_files(root, excludes)
    if not files:
        print(f"[project-map] no source files found under {root} "
              f"(looked for {', '.join(sorted(EXTS))}).")
        return

    if mode == "affected":
        run_affected(root, files, manifests, depth, opts)
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

    generated = {f for f in files if is_generated(root, f)}
    mod_of = {f: module_key(f, depth) for f in files}
    file_deps, file_externals = scan_imports(root, files, Resolver(root, files, manifests))
    uses, used_by = module_graph(file_deps, mod_of)

    # Every module's block is regenerated (and written only if it changed):
    # an edit in one module can change another's "Used by".
    modinfo = {}
    needs_summary = []
    for key, fs in sorted(groups.items()):
        safe = safe_name(key)
        doc_rel = f"modules/{safe}.md"
        docfile = modules_dir / f"{safe}.md"
        syms = module_symbols(by_file, [f for f in fs if f not in generated])
        n_gen = len(module_symbols(by_file, [f for f in fs if f in generated]))
        externals = sorted(set().union(*(file_externals.get(f, set()) for f in fs)))
        inner = auto_block(fs, generated, syms, n_gen, uses.get(key, set()), used_by.get(key, set()), externals)

        if docfile.exists():
            text = upsert_block(docfile.read_text(), AUTO_START, AUTO_END, inner)
            write_if_changed(docfile, text)
            summary_todo = SUMMARY_TODO in text
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
        modblock = module_index(modinfo, depth, parse_oneliners(text), uses)
        write_if_changed(archfile, upsert_block(text, MOD_START, MOD_END, modblock))
    else:
        modblock = module_index(modinfo, depth, {}, uses)
        archfile.write_text(fresh_arch(root.name, modblock,
                                       detect_entries(root, [f for f in files if f not in generated]),
                                       detect_build(root)))

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

    pointer_written, pointer_current = write_pointer(root)

    # report for the agent
    n_edges = sum(len(v) for v in uses.values())
    print(f"[project-map] {mode}: indexed {len(files)} files into {len(groups)} modules "
          f"(depth {depth}), {n_edges} internal module dependencies. tags -> {OUT_DIR}/tags")
    if used_parsers:
        print(f"  bundled parser used for: {', '.join(sorted(used_parsers))} "
              f"(stock ctags has no native parser).")
    if generated:
        print(f"  {len(generated)} generated file(s): kept in tags, left out of `Public symbols`.")
    if oversize:
        print(f"  skipped {len(oversize)} file(s) over 1 MB: {', '.join(oversize[:5])}"
              f"{', …' if len(oversize) > 5 else ''}")
    if n_excluded:
        print(f"  excluded {n_excluded} source file(s) via {OUT_DIR}/{CONFIG_FILE}.")
    for key in removed:
        print(f"  removed module: {key}")
    if pointer_written:
        print(f"  pointer block written to: {', '.join(pointer_written)}")
    elif pointer_current:
        print(f"  pointer block up to date in: {', '.join(pointer_current)}")
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
