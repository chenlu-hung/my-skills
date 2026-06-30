#!/usr/bin/env python3
"""Periodic runner (driven by launchd): launch scheduled jobs once their
start time has passed.

One global lock serializes everything — overlapping launchd ticks exit
immediately, and due jobs run one at a time (a long job blocks later ones,
which is the safer default for spreading usage rather than burning several
windows in parallel). Each job runs `claude -p "<prompt>"` in its directory
with the job's permission mode. If that run itself hits a usage limit, the
globally-installed autocontinue StopFailure hook queues it for resume after
reset — the two tools compose, this runner just records the launch result.
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import action_common as ac

LOCK_DIR = os.path.join(ac.BASE, "runner.lock")
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


def retire(path, entry, status, dest_dir):
    entry["status"] = status
    entry["retired_at"] = time.time()
    ac.write_entry(path, entry)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(dest):
        os.unlink(dest)
    shutil.move(path, dest)


def run_job(entry, cfg, claude_bin):
    cwd = entry.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    env = os.environ.copy()
    path_parts = [os.path.dirname(claude_bin), "/opt/homebrew/bin", "/usr/local/bin"]
    path_parts += env.get("PATH", "/usr/bin:/bin").split(":")
    env["PATH"] = ":".join(dict.fromkeys(p for p in path_parts if p))

    cmd = [claude_bin, "-p", entry["prompt"]]
    cmd += perm_args(entry.get("permission_mode", cfg["permission_mode"]))
    model = entry.get("model") or cfg.get("model")
    if model:
        cmd += ["--model", model]

    log_path = os.path.join(ac.RUN_LOG_DIR, entry["id"] + ".log")
    with open(log_path, "a") as lf:
        lf.write(
            "\n===== %s start id=%s cwd=%s mode=%s =====\n"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                entry["id"],
                cwd,
                entry.get("permission_mode", cfg["permission_mode"]),
            )
        )
        lf.flush()
        return subprocess.call(
            cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, env=env,
            stdin=subprocess.DEVNULL,
        )


def recover_stale_running():
    """We hold the lock, so any 'running' entry is from a crashed runner."""
    for path, entry in ac.scan_entries():
        if entry.get("status") == "running":
            entry["status"] = "scheduled"
            ac.write_entry(path, entry)
            ac.log("runner", "recovered stale running job %s" % entry["id"])


def main():
    ac.ensure_dirs()
    cfg = ac.load_config()
    if not acquire_lock():
        return 0
    try:
        recover_stale_running()
        while True:
            now = time.time()
            due = [
                (path, entry)
                for path, entry in ac.scan_entries()
                if entry.get("status") == "scheduled" and now >= entry.get("start_at", 0)
            ]
            if not due:
                break
            # Run the one whose start time is earliest first.
            path, entry = min(due, key=lambda pe: pe[1].get("start_at", 0))
            label = entry.get("label") or os.path.basename(entry.get("cwd", "").rstrip("/")) or entry["id"]

            claude_bin = resolve_claude(cfg)
            if not claude_bin:
                ac.log("runner", "claude binary not found (%s); retry next tick" % cfg["claude_bin"])
                break

            entry["status"] = "running"
            entry["started_at"] = now
            ac.write_entry(path, entry)
            ac.notify(cfg, "Action 啟動", "%s：排程任務開始執行" % label)
            ac.log("runner", "running %s (scheduled %s)" % (entry["id"], ac.fmt_time(entry.get("start_at", now))))

            rc = run_job(entry, cfg, claude_bin)

            current = ac.read_entry(path)
            if current is None:
                ac.log("runner", "job %s vanished during run" % entry["id"])
                continue
            if rc == 0:
                retire(path, current, "done", ac.DONE_DIR)
                ac.notify(cfg, "Action 完成", "%s：排程任務正常結束" % label)
                ac.log("runner", "%s finished cleanly" % entry["id"])
            else:
                # Nonzero may mean the run hit a usage limit; if autocontinue is
                # installed it has already queued a resume independently. We just
                # record the launch outcome here.
                retire(path, current, "failed", ac.DEAD_DIR)
                ac.notify(cfg, "Action 異常", "%s：結束碼 %d（若撞 limit，autocontinue 會接手復活）" % (label, rc))
                ac.log("runner", "%s exited rc=%d" % (entry["id"], rc))
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
