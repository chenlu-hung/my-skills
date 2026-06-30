#!/usr/bin/env python3
"""`action` CLI: schedule a Claude Code task to start at a wall-clock time.

    action add --at 3am --cwd ~/proj "把剩下的 docstring 補完並跑測試"
    action add --at "2026-06-29 02:30" --label nightly-refactor "..."
    action list
    action rm <id>
    action run            # run the due-job sweep once (what launchd calls)

Jobs are stored as JSON in ~/.claude/action/queue/ and launched by the
launchd runner once their start time passes. See README.md.
"""
import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import action_common as ac


def cmd_add(args):
    cfg = ac.load_config()
    start_at = ac.parse_clock(args.at)
    if start_at is None:
        sys.exit("error: could not parse --at %r (try 3am, 3:30pm, 15:00, or '2026-06-29 02:30')" % args.at)

    prompt = args.prompt
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        sys.exit("error: no prompt given (pass it as an argument or on stdin)")

    cwd = os.path.abspath(os.path.expanduser(args.cwd)) if args.cwd else os.getcwd()
    mode = args.mode or cfg["permission_mode"]

    ac.ensure_dirs()
    job_id = ac.new_id()
    entry = {
        "id": job_id,
        "label": args.label,
        "prompt": prompt,
        "cwd": cwd,
        "start_at": start_at,
        "permission_mode": mode,
        "model": args.model or cfg.get("model"),
        "status": "scheduled",
        "created_at": time.time(),
    }
    ac.write_entry(ac.entry_path(job_id), entry)
    eta = ac.fmt_time(start_at)
    mins = int((start_at - time.time()) / 60)
    print("scheduled %s for %s (~%d min)  cwd=%s  mode=%s" % (job_id, eta, mins, cwd, mode))
    ac.notify(cfg, "Action 已排程", "%s：%s 啟動" % (args.label or os.path.basename(cwd), eta))


def cmd_list(args):
    ac.ensure_dirs()
    rows = ac.scan_entries()
    if args.all:
        for d in (ac.DONE_DIR, ac.DEAD_DIR):
            rows += ac.scan_entries(d)
    if not rows:
        print("(no jobs)")
        return
    rows.sort(key=lambda pe: pe[1].get("start_at", 0))
    for _, e in rows:
        label = e.get("label") or os.path.basename(e.get("cwd", "").rstrip("/"))
        preview = (e.get("prompt") or "").replace("\n", " ")
        if len(preview) > 50:
            preview = preview[:47] + "..."
        print(
            "%-26s %-8s %s  %-14s  %s"
            % (e["id"], e.get("status", "?"), ac.fmt_time(e.get("start_at", 0)), label, preview)
        )


def cmd_rm(args):
    for jid in args.ids:
        path = ac.entry_path(jid)
        if os.path.exists(path):
            os.remove(path)
            print("removed", jid)
        else:
            # allow removing by exact filename match in queue
            hit = False
            for p, e in ac.scan_entries():
                if e.get("id") == jid:
                    os.remove(p)
                    print("removed", jid)
                    hit = True
            if not hit:
                print("not found:", jid)


def cmd_run(args):
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "action_runner.py")
    os.execv(sys.executable, [sys.executable, runner])


def main():
    p = argparse.ArgumentParser(prog="action", description="Schedule Claude Code tasks to start at a wall-clock time.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="schedule a new job")
    a.add_argument("--at", required=True, help="start time: 3am, 3:30pm, 15:00, or '2026-06-29 02:30'")
    a.add_argument("--cwd", help="directory to run in (default: current dir)")
    a.add_argument("--label", help="short label for notifications/listing")
    a.add_argument("--mode", choices=["bypassPermissions", "acceptEdits", "plan", "default"], help="permission mode (default: config)")
    a.add_argument("--model", help="model id for this job (e.g. sonnet, haiku)")
    a.add_argument("prompt", nargs="?", help="the task prompt (or pipe it on stdin)")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="list scheduled jobs")
    l.add_argument("--all", action="store_true", help="also show done/failed jobs")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("rm", help="remove scheduled job(s) by id")
    r.add_argument("ids", nargs="+")
    r.set_defaults(func=cmd_rm)

    rn = sub.add_parser("run", help="run the due-job sweep once (what launchd calls)")
    rn.set_defaults(func=cmd_run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
