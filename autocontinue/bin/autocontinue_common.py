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
    "resume_prompt": (
        "你剛才因為 usage limit 而中斷，現在額度已重置。"
        "請從上次的進度繼續完成原本的任務；若任務其實已完成，確認狀態後即可結束。"
    ),
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
