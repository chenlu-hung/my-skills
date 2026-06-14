#!/usr/bin/env python3
"""Periodic checker run by launchd: resume rate-limited sessions after reset.

One global lock serializes everything — overlapping launchd ticks exit
immediately, and queued sessions are resumed one at a time in the order
they were interrupted. A resume runs `claude --resume <id> -p` with the
session's original permission mode and AUTOCONTINUE_ROOT exported, so a
repeat rate limit inside the resumed run re-queues the same root entry
(with its attempt count) via the StopFailure hook.
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autocontinue_common as ac

LOCK_DIR = os.path.join(ac.BASE, "checker.lock")
LOCK_PID = os.path.join(LOCK_DIR, "pid")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def acquire_lock():
    for _ in range(2):
        try:
            os.mkdir(LOCK_DIR)
            with open(LOCK_PID, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                with open(LOCK_PID) as f:
                    pid = int(f.read().strip())
            except (OSError, ValueError):
                pid = None
            if pid is not None and pid_alive(pid):
                return False
            shutil.rmtree(LOCK_DIR, ignore_errors=True)
    return False


def release_lock():
    shutil.rmtree(LOCK_DIR, ignore_errors=True)


def ready_time(entry, cfg):
    if entry.get("reset_at"):
        return entry["reset_at"] + cfg["resume_buffer_sec"]
    return entry.get("interrupted_at", 0) + cfg["min_retry_wait_sec"]


def perm_args(mode):
    if mode == "bypassPermissions":
        return ["--dangerously-skip-permissions"]
    if mode in ("acceptEdits", "plan"):
        return ["--permission-mode", mode]
    return []


def resolve_claude(cfg):
    binary = cfg["claude_bin"]
    if os.path.sep in binary:
        return binary if os.path.exists(binary) else None
    search = ":".join(
        [
            os.environ.get("PATH", ""),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.expanduser("~/.local/bin"),
        ]
    )
    return shutil.which(binary, path=search)


def scan_entries():
    entries = []
    for name in sorted(os.listdir(ac.QUEUE_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(ac.QUEUE_DIR, name)
        entry = ac.read_entry(path)
        if entry:
            entries.append((path, entry))
    return entries


def retire(path, entry, status, dest_dir):
    entry["status"] = status
    entry["retired_at"] = time.time()
    ac.write_entry(path, entry)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(dest):
        os.unlink(dest)
    shutil.move(path, dest)


def run_resume(entry, cfg, claude_bin):
    root = entry["root_id"]
    cwd = ac.resume_dir(entry.get("transcript_path"), entry.get("cwd"))
    if not cwd or not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    env = os.environ.copy()
    env["AUTOCONTINUE_ROOT"] = root
    path_parts = [os.path.dirname(claude_bin), "/opt/homebrew/bin", "/usr/local/bin"]
    path_parts += env.get("PATH", "/usr/bin:/bin").split(":")
    env["PATH"] = ":".join(dict.fromkeys(p for p in path_parts if p))

    cmd = [claude_bin, "--resume", entry["session_id"], "-p", cfg["resume_prompt"]]
    cmd += perm_args(entry.get("permission_mode", "default"))

    log_path = os.path.join(
        ac.SESSION_LOG_DIR, os.path.basename(ac.entry_path(root)).replace(".json", ".log")
    )
    with open(log_path, "a") as lf:
        lf.write(
            "\n===== %s attempt %d session=%s cwd=%s =====\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), entry["attempts"], entry["session_id"], cwd)
        )
        lf.flush()
        return subprocess.call(
            cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, env=env,
            stdin=subprocess.DEVNULL,
        )


def recover_stale_running():
    """We hold the lock, so any 'running' entry is from a crashed checker."""
    for path, entry in scan_entries():
        if entry.get("status") == "running":
            entry["status"] = "waiting"
            ac.write_entry(path, entry)
            ac.log("checker", "recovered stale running entry %s" % entry["root_id"])


def main():
    ac.ensure_dirs()
    cfg = ac.load_config()
    if not acquire_lock():
        return 0
    try:
        recover_stale_running()
        while True:
            now = time.time()
            actionable = [
                (path, entry)
                for path, entry in scan_entries()
                if entry.get("status") == "waiting" and now >= ready_time(entry, cfg)
            ]
            if not actionable:
                break
            path, entry = min(actionable, key=lambda pe: pe[1].get("interrupted_at", 0))
            project = os.path.basename(entry.get("cwd", "").rstrip("/")) or "?"

            if entry["attempts"] >= cfg["max_attempts"]:
                retire(path, entry, "abandoned", ac.DEAD_DIR)
                ac.notify(
                    cfg,
                    "Autocontinue 放棄",
                    "%s：已達 %d 次上限，停止自動續跑" % (project, cfg["max_attempts"]),
                )
                ac.log("checker", "abandoned %s after %d attempts" % (entry["root_id"], entry["attempts"]))
                continue

            claude_bin = resolve_claude(cfg)
            if not claude_bin:
                ac.log("checker", "claude binary not found (%s); will retry next tick" % cfg["claude_bin"])
                break

            entry["attempts"] += 1
            entry["status"] = "running"
            entry["last_attempt_at"] = now
            ac.write_entry(path, entry)
            ac.notify(
                cfg,
                "Autocontinue 復活",
                "%s：第 %d/%d 次接力" % (project, entry["attempts"], cfg["max_attempts"]),
            )
            ac.log("checker", "resuming %s attempt %d" % (entry["root_id"], entry["attempts"]))

            rc = run_resume(entry, cfg, claude_bin)

            current = ac.read_entry(path)
            if current is None:
                ac.log("checker", "entry %s vanished during run" % entry["root_id"])
                continue
            if current.get("status") == "waiting":
                # The resumed run hit the limit again (or was still limited);
                # the hook already re-queued it with a fresh reset time.
                ac.log("checker", "%s re-queued by hook (rc=%d)" % (entry["root_id"], rc))
                continue
            if rc == 0:
                retire(path, current, "done", ac.DONE_DIR)
                ac.log("checker", "%s finished cleanly" % entry["root_id"])
            else:
                retire(path, current, "failed", ac.DEAD_DIR)
                ac.notify(
                    cfg,
                    "Autocontinue 放棄",
                    "%s：續跑異常結束（exit %d），已停止" % (project, rc),
                )
                ac.log("checker", "%s failed with rc=%d" % (entry["root_id"], rc))
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
