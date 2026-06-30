#!/usr/bin/env python3
"""Shared helpers for the action scheduler (the `action` CLI + the runner).

`action` is the proactive sibling of `autocontinue`: instead of reacting to a
usage limit *after* it is hit, it parks a task now and launches it headlessly
at a wall-clock time you pick, so heavy/batch work runs in an off-peak window
and spreads load across the rolling usage limits. A launchd agent polls the
queue every few minutes; when a job's start time has passed it runs
`claude -p "<prompt>"` in the job's directory, unattended.
"""
import json
import os
import re
import tempfile
import time
import uuid
import subprocess
from datetime import datetime

BASE = os.environ.get("ACTION_BASE") or os.path.expanduser("~/.claude/action")
QUEUE_DIR = os.path.join(BASE, "queue")
LOG_DIR = os.path.join(BASE, "logs")
RUN_LOG_DIR = os.path.join(LOG_DIR, "runs")
DEAD_DIR = os.path.join(LOG_DIR, "dead")
DONE_DIR = os.path.join(LOG_DIR, "done")
CONFIG_PATH = os.path.join(BASE, "config.json")

DEFAULT_CONFIG = {
    "claude_bin": "claude",
    # Default permission mode for newly scheduled jobs. "bypassPermissions"
    # runs fully unattended (no one is around at 3am to click "allow"); a job
    # can override this per-entry. "default"/"acceptEdits"/"plan" are accepted
    # but a "default" job will stall on the first tool that needs approval.
    "permission_mode": "bypassPermissions",
    # None lets the run use claude's own default model. Set to "sonnet" /
    # "haiku" / a model id to run scheduled jobs at a cheaper rate.
    "model": None,
    "notify": True,
}


def ensure_dirs():
    for d in (BASE, QUEUE_DIR, LOG_DIR, RUN_LOG_DIR, DEAD_DIR, DONE_DIR):
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


def fmt_time(epoch):
    return time.strftime("%m/%d %H:%M", time.localtime(epoch))


def parse_clock(text, now=None):
    """Parse an absolute wall-clock start time into an epoch (local time).

    Accepts, with an optional leading `YYYY-MM-DD` date:
        3am  3pm  3:30am  9:05pm  15:00  09:30  23:5  (24h needs a colon)
    Without a date, returns the *next* occurrence of that time of day (today if
    still in the future, otherwise tomorrow). Returns None if unparseable.
    """
    now = time.time() if now is None else now
    text = (text or "").strip().lower()

    date_part = None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ t]+(.*)$", text)
    if m:
        date_part = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        text = m.group(4).strip()

    hour = minute = None
    # 12-hour with am/pm: "3am", "3:30pm", "9 am"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?$", text)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3) == "p":
            hour += 12
        minute = int(m.group(2) or 0)
    else:
        # 24-hour, colon required to disambiguate from a bare 12h hour: "15:00"
        m = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))

    if hour is None or not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None

    lt = time.localtime(now)
    if date_part:
        y, mo, d = date_part
    else:
        y, mo, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    candidate = time.mktime((y, mo, d, hour, minute, 0, 0, 0, -1))
    if not date_part and candidate <= now:
        candidate += 86400  # already passed today -> next day
    return candidate


def new_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def entry_path(job_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
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


def scan_entries(directory=QUEUE_DIR):
    entries = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        entry = read_entry(path)
        if entry:
            entries.append((path, entry))
    return entries
