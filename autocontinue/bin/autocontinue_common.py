#!/usr/bin/env python3
"""Shared helpers for the autocontinue hook and checker."""
import json
import os
import re
import subprocess
import tempfile
import time

BASE = os.environ.get("AUTOCONTINUE_BASE") or os.path.expanduser("~/.claude/autocontinue")
QUEUE_DIR = os.path.join(BASE, "queue")
LOG_DIR = os.path.join(BASE, "logs")
SESSION_LOG_DIR = os.path.join(LOG_DIR, "sessions")
DEAD_DIR = os.path.join(LOG_DIR, "dead")
DONE_DIR = os.path.join(LOG_DIR, "done")
CONFIG_PATH = os.path.join(BASE, "config.json")

DEFAULT_CONFIG = {
    "claude_bin": "claude",
    "max_attempts": 10,
    "min_retry_wait_sec": 900,
    "resume_buffer_sec": 120,
    # None keeps the resumed session's own model. Set to "sonnet" / "haiku" / a
    # model id to re-read the transcript at a cheaper input rate on every revival
    # (applies to both resume modes below).
    "resume_model": None,
    # "session": resume the same session — its full transcript is replayed (and,
    #   hours after a reset, re-read at full price because the prompt cache is
    #   cold) on every revival. Lossless, but cost scales with transcript size.
    # "handoff": on the *first* revival, start a fresh session seeded only with a
    #   pointer to the original transcript instead of replaying it; the new (small)
    #   session is then resumed normally on any later revival. Cheaper, but the
    #   fresh session only knows what it reads back from the transcript on demand.
    # "inject": don't run claude ourselves at all — type the resume prompt
    #   straight into the *original kitty window* (the TUI you were watching) via
    #   kitty remote control, so the resume happens visibly, in place, where you
    #   can take over. Requires kitty with `allow_remote_control` + `listen_on`
    #   enabled and that window still open; otherwise it transparently falls back
    #   to a headless "session" resume so the work is never dropped.
    "resume_mode": "session",
    "resume_prompt": (
        "你剛才因為 usage limit 而中斷，現在額度已重置。"
        "請從上次的進度繼續完成原本的任務；若任務其實已完成，確認狀態後即可結束。"
    ),
    # Seed prompt for handoff mode. {transcript_path} is substituted (via
    # str.replace, so stray braces in a customised prompt are harmless).
    "handoff_prompt": (
        "你正在接手一個因 usage limit 中斷的任務，額度已重置。"
        "上一個 session 的完整對話記錄（JSONL，每行一則訊息）在：\n{transcript_path}\n"
        "請先只讀取該檔案的結尾部分（例如 `tail -n 80`）來掌握原本的任務目標與中斷時的進度；"
        "需要更早的脈絡（某個決策的理由、先前讀過的檔案內容）時，再針對性地 grep 那個檔案，"
        "不要整份讀進來。接著從中斷處繼續完成原本的任務；若其實已完成，確認狀態後即可結束。"
    ),
    # resume_mode "inject" only: path to the kitty binary used for remote control.
    "kitty_bin": "kitty",
    # resume_mode "inject" only: an injected resume runs in your TUI where we
    # can't watch its exit, so an injected entry that never re-hits the limit is
    # assumed finished after this many seconds (default 6h) and retired to done/.
    "inject_ttl_sec": 21600,
    "notify": True,
}


def ensure_dirs():
    for d in (BASE, QUEUE_DIR, LOG_DIR, SESSION_LOG_DIR, DEAD_DIR, DONE_DIR):
        os.makedirs(d, exist_ok=True)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def log(name, message):
    ensure_dirs()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(LOG_DIR, name + ".log"), "a") as f:
        f.write("[%s] %s\n" % (stamp, message))


def notify(cfg, title, message):
    if not cfg.get("notify", True):
        return
    script = "display notification %s with title %s" % (
        json.dumps(message, ensure_ascii=False),
        json.dumps(title, ensure_ascii=False),
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass


def parse_reset_time(message, now=None):
    """Best-effort extraction of the limit-reset epoch from an error message.

    Returns epoch seconds, or None when the message carries no usable time.
    """
    if not message:
        return None
    now = time.time() if now is None else now

    m = re.search(r"\|(\d{13})\b", message)
    if m:
        return int(m.group(1)) / 1000.0
    m = re.search(r"\|(\d{10})\b", message)
    if m:
        return float(m.group(1))
    m = re.search(r"\b(1[6-9]\d{8})\b", message)
    if m:
        return float(m.group(1))

    m = re.search(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?",
        message,
    )
    if m:
        try:
            from datetime import datetime

            text = m.group(0).replace("Z", "+00:00")
            text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
            dt = datetime.fromisoformat(text)
            ts = dt.timestamp() if dt.tzinfo else time.mktime(dt.timetuple())
            if ts > now - 60:
                return ts
        except ValueError:
            pass

    m = re.search(r"reset\w*\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", message, re.I)
    if not m:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", message, re.I)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == "p":
            hour += 12
        minute = int(m.group(2) or 0)
        lt = time.localtime(now)
        candidate = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
        if candidate <= now:
            candidate += 86400
        return candidate

    return None


def fmt_time(epoch):
    return time.strftime("%m/%d %H:%M", time.localtime(epoch))


def render_handoff_prompt(cfg, transcript_path):
    """Seed prompt for a fresh handoff session.

    Uses str.replace (not str.format) so a user-customised prompt with stray
    braces can't raise. Falls back to the default template if unset.
    """
    template = cfg.get("handoff_prompt") or DEFAULT_CONFIG["handoff_prompt"]
    return template.replace("{transcript_path}", transcript_path or "")


def encode_project(path):
    """Replicate Claude Code's project-dir encoding (every non-alnum -> '-').

    Claude stores a session under ~/.claude/projects/<encode(launch_dir)>/, so
    this lets us recognise which real directory a transcript belongs to.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def resume_dir(transcript_path, cwd):
    """Directory to run `claude --resume <id>` from.

    A session id resolves only within the project of the directory `claude` was
    launched in (its root). The StopFailure payload's `cwd` can be a *sub*-
    directory of that root (e.g. a skill working in `.claude/app`), and resuming
    from there lands in a different project where the session does not exist
    ("No conversation found"). So we walk up from cwd until a parent encodes to
    the same name as the transcript's project directory, and resume from there.
    Falls back to cwd when nothing matches (e.g. payloads without a transcript).
    """
    if not cwd:
        return cwd
    if not transcript_path:
        return cwd
    want = os.path.basename(os.path.dirname(transcript_path))
    d = os.path.abspath(os.path.expanduser(cwd))
    while True:
        if encode_project(d) == want and os.path.isdir(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return cwd
        d = parent


def entry_path(root_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", root_id)
    return os.path.join(QUEUE_DIR, safe + ".json")


def read_entry(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_entry(path, entry):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
