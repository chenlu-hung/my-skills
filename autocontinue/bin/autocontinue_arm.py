#!/usr/bin/env python3
"""Hand the post-reset wait back to Claude Code by driving /rate-limit-options.

Claude Code can sit out a usage limit and continue the task by itself, but on
some accounts it never arms that wait on its own: the limit message appears,
the session stops, and the "Usage limit reached - continuing automatically at
..." notice that marks an armed wait never shows up. The machinery is still
reachable by hand -- /rate-limit-options offers "Wait here, then continue
automatically at ...", and picking it arms exactly the same wait.

This script does the picking, by remote-controlling the kitty window the
rate-limited session lives in. Arming in place is far cheaper than the queue
path this repo falls back to: the session keeps its context, its permission
mode and its warm prompt cache, whereas a `claude --resume` hours later re-reads
the entire transcript at full input price.

kitty's send-key reports success even when it reached no window at all, so
nothing here trusts a return code: every step is confirmed by reading the
screen back with get-text. Anything unexpected backs out with Escape and exits
non-zero, leaving the queue entry as "waiting" so the old resume path still
covers the session.
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autocontinue_common as ac

# The menu entry we want. The label continues with "at <time>", "shortly" or
# "when the limit resets" depending on how far off the reset is, so match the
# common prefix only.
TARGET = "Wait here, then continue automatically"
# Shown in place of TARGET when the wait is already armed -- typographic
# apostrophe in the real label, hence the regex.
ALREADY_ARMED = re.compile(r"Don.t continue automatically")
# Either of these on screen means the wait is now armed: the first is the
# dialog's confirmation line, the second the persistent status notice.
ARMED_MARKERS = ("will continue automatically", "continuing automatically")
# Claude Code marks both the input prompt and the selected menu row with this.
CURSOR = "❯"


def base_cmd(kitty_bin, listen):
    return [kitty_bin, "@", "--to", listen]


def get_text(base, match):
    """Current visible screen of the target window, or None if unreadable."""
    try:
        out = subprocess.run(
            base + ["get-text", "--match", match, "--extent", "screen"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def send_text(base, match, text):
    try:
        return subprocess.call(
            base + ["send-text", "--match", match, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return 1


def send_key(base, match, key):
    """Send one key. Return code is meaningless (kitty always reports success),
    so callers must verify the effect by reading the screen."""
    try:
        subprocess.call(
            base + ["send-key", "--match", match, key],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def wait_for(base, match, predicate, timeout, interval=0.25):
    """Poll the screen until predicate(text) holds. Returns the text, or None."""
    deadline = time.time() + timeout
    while True:
        text = get_text(base, match)
        if text is not None and predicate(text):
            return text
        if time.time() >= deadline:
            return None
        time.sleep(interval)


def cursor_lines(lines):
    return [i for i, line in enumerate(lines) if CURSOR in line]


def input_box_is_clear(text):
    """True when the only cursor on screen is an empty input prompt.

    Guards against typing into a box the user is already using: if they have a
    half-written prompt sitting there, appending /rate-limit-options to it would
    both lose their text and submit something they never wrote. When in doubt,
    say no -- the caller then falls back to the queue and nothing is touched.
    """
    lines = text.splitlines()
    marked = cursor_lines(lines)
    if len(marked) != 1:
        return False
    rest = lines[marked[0]].split(CURSOR, 1)[1]
    return rest.strip() == ""


def selected_line(lines, target_idx):
    """Index of the highlighted menu row, or None.

    The input prompt and the selected menu row carry the same cursor glyph, so
    the input box has to be ruled out before picking: it is the marked line with
    nothing after the glyph (a menu row always has a label), or one still
    holding the command we just typed. Whatever marked rows are left, the one
    nearest the target is the menu's -- menu rows sit together.
    """
    candidates = []
    for i in cursor_lines(lines):
        rest = lines[i].split(CURSOR, 1)[1].strip()
        if not rest or rest.startswith("/rate-limit-options"):
            continue
        candidates.append(i)
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs(i - target_idx))


def find_target(lines):
    for i, line in enumerate(lines):
        if TARGET in line:
            return i
    return None


def escape(base, match):
    send_key(base, match, "escape")


def drive(base, match, cfg):
    """Open the dialog, select the wait entry, confirm it took.

    Returns one of: armed, already_armed, no_window, no_screen, input_busy,
    type_failed, no_dialog, lost_dialog, no_move, not_reached, unconfirmed.
    """
    # Let the TUI finish drawing the limit message and settle back at the prompt.
    time.sleep(cfg.get("arm_settle_sec", 1.5))

    text = get_text(base, match)
    if text is None:
        return "no_screen"
    if ALREADY_ARMED.search(text) or any(m in text for m in ARMED_MARKERS):
        return "already_armed"
    if not input_box_is_clear(text):
        return "input_busy"

    if send_text(base, match, "/rate-limit-options") != 0:
        return "type_failed"
    # A newline bundled into send-text arrives as part of the same paste burst
    # and Claude Code reads that as "insert a line", not "submit"; the Return
    # has to be a separate key event, after the paste has settled.
    time.sleep(0.4)
    if wait_for(base, match, lambda s: "/rate-limit-options" in s, 3.0) is None:
        return "type_failed"
    send_key(base, match, "enter")

    text = wait_for(
        base, match,
        lambda s: TARGET in s or ALREADY_ARMED.search(s) is not None,
        cfg.get("arm_dialog_timeout_sec", 6.0),
    )
    if text is None:
        escape(base, match)
        return "no_dialog"
    if ALREADY_ARMED.search(text):
        escape(base, match)
        return "already_armed"

    # Walk the highlight onto the target row. Direction flips once if the list
    # turns out not to wrap; a second stall means we are not really driving the
    # menu and we back out rather than press Enter on an unknown row.
    step = cfg.get("arm_step_sec", 0.15)
    direction = "down"
    previous = None
    stalls = 0
    for _ in range(int(cfg.get("arm_max_steps", 16))):
        text = get_text(base, match)
        if text is None:
            escape(base, match)
            return "lost_dialog"
        lines = text.splitlines()
        target_idx = find_target(lines)
        if target_idx is None:
            escape(base, match)
            return "lost_dialog"
        current = selected_line(lines, target_idx)
        if current is None:
            escape(base, match)
            return "lost_dialog"
        if current == target_idx:
            send_key(base, match, "enter")
            break
        if current == previous:
            stalls += 1
            if stalls == 1:
                direction = "up" if direction == "down" else "down"
            else:
                escape(base, match)
                return "no_move"
        else:
            stalls = 0
        previous = current
        send_key(base, match, direction)
        time.sleep(step)
    else:
        escape(base, match)
        return "not_reached"

    confirmed = wait_for(
        base, match,
        lambda s: any(m in s for m in ARMED_MARKERS),
        cfg.get("arm_dialog_timeout_sec", 6.0),
    )
    return "armed" if confirmed is not None else "unconfirmed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="queue entry root id")
    parser.add_argument("--window", required=True, help="kitty window id")
    parser.add_argument("--listen", required=True, help="kitty listen-on socket")
    args = parser.parse_args()

    cfg = ac.load_config()
    kitty_bin = ac.resolve_kitty(cfg)
    if not kitty_bin:
        ac.log("arm", "root=%s no kitty binary, leaving entry queued" % args.root)
        return 1
    if not ac.kitty_window_alive(kitty_bin, args.listen, args.window):
        ac.log("arm", "root=%s window %s gone, leaving entry queued"
               % (args.root, args.window))
        return 1

    base = base_cmd(kitty_bin, args.listen)
    match = "id:%s" % args.window
    try:
        result = drive(base, match, cfg)
    except Exception as exc:  # never leave a half-driven dialog open
        escape(base, match)
        ac.log("arm", "root=%s crashed (%s), leaving entry queued" % (args.root, exc))
        return 1

    ac.log("arm", "root=%s result=%s" % (args.root, result))
    if result not in ("armed", "already_armed"):
        return 1

    # Claude Code now owns the wait, so keep the checker off this entry. It
    # stays in the queue (rather than being retired) because a session that
    # re-hits the limit gets flipped back to "waiting" by the hook, and the
    # chain's attempt count has to survive that round trip.
    path = ac.entry_path(args.root)
    entry = ac.read_entry(path)
    if entry is not None:
        entry["status"] = "armed"
        entry["armed_at"] = time.time()
        ac.write_entry(path, entry)
    project = os.path.basename((entry or {}).get("cwd", "").rstrip("/")) or "session"
    ac.notify(cfg, "Claude 已就地排程接續",
              "%s：額度重置後由 Claude Code 自己續跑，保持視窗開著" % project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
