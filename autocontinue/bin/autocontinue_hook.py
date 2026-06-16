#!/usr/bin/env python3
"""StopFailure hook: queue a rate-limited session for automatic resume.

Claude Code invokes this with the StopFailure payload on stdin. Output and
exit code are ignored by Claude Code, so this script only has side effects:
it records the raw payload (for calibrating the reset-time parser), writes
or refreshes a queue entry, and posts a macOS notification.

When the interrupted session is itself an autocontinue resume run, the
checker has set AUTOCONTINUE_ROOT in our environment; the chain then keeps
accumulating attempts under the original root entry instead of starting a
fresh count with the forked session id.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autocontinue_common as ac


def main():
    raw = sys.stdin.read()
    ac.ensure_dirs()
    with open(os.path.join(ac.LOG_DIR, "stopfailure-raw.jsonl"), "a") as f:
        f.write(raw.strip().replace("\n", " ") + "\n")

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}

    # Claude Code's StopFailure payload carries the error kind in "error" and
    # the human-readable text (with the reset time) in "last_assistant_message".
    # Older/assumed names are accepted too in case the schema varies.
    error_type = payload.get("error_type") or payload.get("error")
    error_message = (
        payload.get("error_message") or payload.get("last_assistant_message") or ""
    )
    is_rate_limit = error_type == "rate_limit" or (
        error_type is None and "limit" in error_message.lower()
    )
    if not is_rate_limit:
        return 0

    sid = payload.get("session_id")
    if not sid:
        ac.log("hook", "rate_limit payload without session_id, skipped")
        return 0

    cfg = ac.load_config()
    root = os.environ.get("AUTOCONTINUE_ROOT") or sid
    now = time.time()
    path = ac.entry_path(root)
    prev = ac.read_entry(path) or {}
    reset_at = ac.parse_reset_time(error_message, now)

    entry = {
        "root_id": root,
        "session_id": sid,
        "cwd": payload.get("cwd") or prev.get("cwd") or os.path.expanduser("~"),
        "transcript_path": payload.get("transcript_path") or prev.get("transcript_path"),
        "permission_mode": payload.get("permission_mode")
        or prev.get("permission_mode")
        or "default",
        # Preserve the handoff "already seeded" flag so a re-queued handoff
        # session is resumed normally next time instead of seeding again.
        "seeded": prev.get("seeded", False),
        "interrupted_at": now,
        "reset_at": reset_at,
        "attempts": prev.get("attempts", 0),
        "status": "waiting",
        "error_message": error_message[:500],
    }
    ac.write_entry(path, entry)

    if reset_at:
        eta = "預計 " + ac.fmt_time(reset_at + cfg["resume_buffer_sec"]) + " 復活"
    else:
        eta = "reset 時間未知，每 %d 分鐘嘗試" % (cfg["min_retry_wait_sec"] // 60)
    project = os.path.basename(entry["cwd"].rstrip("/")) or entry["cwd"]
    ac.notify(cfg, "Claude 撞到 usage limit", "%s：已排隊（%s）" % (project, eta))
    ac.log(
        "hook",
        "queued root=%s session=%s reset_at=%s attempts=%d"
        % (root, sid, reset_at, entry["attempts"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
