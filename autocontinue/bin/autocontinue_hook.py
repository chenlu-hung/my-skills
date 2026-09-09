#!/usr/bin/env python3
"""StopFailure hook: queue a rate-limited session for automatic resume.

Claude Code invokes this with the StopFailure payload on stdin. Output and
exit code are ignored by Claude Code, so this script only has side effects:
it records the raw payload (for calibrating the reset-time parser), writes
or refreshes a queue entry, starts the armer that tries to hand the wait to
Claude Code itself, and posts a macOS notification.

When the interrupted session is itself an autocontinue resume run, the
checker has set AUTOCONTINUE_ROOT in our environment; the chain then keeps
accumulating attempts under the original root entry instead of starting a
fresh count with the forked session id.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autocontinue_common as ac


def spawn_armer(root, entry):
    """Start the dialog driver detached, and return whether it got going.

    Claude Code waits for this hook (it has a timeout), and driving the dialog
    takes seconds of screen round-trips, so the armer must not run inline.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autocontinue_arm.py")
    if not os.path.exists(script):
        return False
    try:
        with open(os.devnull, "r+b") as devnull:
            subprocess.Popen(
                [
                    sys.executable, script,
                    "--root", root,
                    "--window", str(entry["kitty_window_id"]),
                    "--listen", entry["kitty_listen_on"],
                ],
                stdin=devnull, stdout=devnull, stderr=devnull,
                start_new_session=True,
            )
    except Exception as exc:
        ac.log("hook", "could not start armer for root=%s: %s" % (root, exc))
        return False
    return True


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
        # For resume_mode "inject": this hook runs inside the rate-limited
        # session's process tree, so when that session lives in a kitty window
        # with remote control on, kitty has exported these into our env. They
        # let the checker later type the resume prompt back into this exact
        # window. Absent (kept from prev) when not in a kitty/RC context.
        "kitty_window_id": os.environ.get("KITTY_WINDOW_ID") or prev.get("kitty_window_id"),
        "kitty_listen_on": os.environ.get("KITTY_LISTEN_ON") or prev.get("kitty_listen_on"),
        "interrupted_at": now,
        "reset_at": reset_at,
        "attempts": prev.get("attempts", 0),
        "status": "waiting",
        "error_message": error_message[:500],
    }
    ac.write_entry(path, entry)

    # Prefer handing the wait to Claude Code itself: drive /rate-limit-options
    # in this session's own kitty window and pick the "wait, then continue"
    # entry. That keeps the session (and its warm prompt cache) alive in place
    # instead of replaying the whole transcript from a launchd resume hours
    # later. The queue entry is written first and left as "waiting", so if the
    # armer can't do it the checker still covers this session; on success the
    # armer flips the entry to "armed" and the checker leaves it alone.
    arming = bool(
        cfg.get("arm_builtin", True)
        and entry["kitty_window_id"]
        and entry["kitty_listen_on"]
    )
    if arming:
        arming = spawn_armer(root, entry)

    if reset_at:
        eta = "預計 " + ac.fmt_time(reset_at + cfg["resume_buffer_sec"]) + " 復活"
    else:
        eta = "reset 時間未知，每 %d 分鐘嘗試" % (cfg["min_retry_wait_sec"] // 60)
    project = os.path.basename(entry["cwd"].rstrip("/")) or entry["cwd"]
    if arming:
        detail = "%s：正在請 Claude Code 就地等待重置（失敗則排隊，%s）" % (project, eta)
    else:
        detail = "%s：已排隊（%s）" % (project, eta)
    ac.notify(cfg, "Claude 撞到 usage limit", detail)
    ac.log(
        "hook",
        "queued root=%s session=%s reset_at=%s attempts=%d"
        % (root, sid, reset_at, entry["attempts"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
