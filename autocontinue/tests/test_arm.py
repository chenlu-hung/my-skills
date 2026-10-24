#!/usr/bin/env python3
"""Drive autocontinue_arm against a fake terminal.

kitty's send-key always reports success, even when it reached no window, so the
armer can only ever know what it did by reading the screen back. That makes the
screen-parsing and menu-walking the part most worth testing, and the part with
no other way to be checked: a real run needs an actual usage limit, and a wrong
turn there presses Enter on a live menu row in the user's own session.

The fake terminal below reproduces the shapes that matter -- the entry sitting
below or above the highlight, a list that wraps or doesn't, the input prompt
sharing its cursor glyph with the selected row, and the dialog simply not
opening. Run: python3 tests/test_arm.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import autocontinue_arm as arm

WAIT = "Wait here, then continue automatically at 6:50pm"
STOP = "Stop and wait for limit to reset"
UPGRADE = "Upgrade your plan"
LOWPRI = "Continue now at lower priority"
CANCEL_WAIT = "Don’t continue automatically"

CFG = {
    "arm_settle_sec": 0,
    "arm_dialog_timeout_sec": 0.3,
    "arm_step_sec": 0,
    "arm_max_steps": 16,
}


class FakeTerm:
    """Just enough of the TUI: a prompt, a select dialog, a confirmation."""

    def __init__(self, menu, selected=0, wraps=True, opens=True, typed=""):
        self.menu = menu
        self.selected = selected
        self.wraps = wraps
        self.opens = opens
        self.input = typed
        self.state = "prompt"
        self.keys = []
        self.entered_on = None

    def send_text(self, text):
        if self.state == "prompt":
            self.input += text
        return 0

    def send_key(self, key):
        self.keys.append(key)
        if key == "escape":
            self.state = "cancelled"
        elif key == "enter":
            if self.state == "prompt":
                if self.opens and self.input.strip() == "/rate-limit-options":
                    self.state = "menu"
                self.input = ""
            elif self.state == "menu":
                self.entered_on = self.menu[self.selected]
                self.state = "confirmed" if self.entered_on == WAIT else "wrong"
        elif key in ("down", "up") and self.state == "menu":
            step = 1 if key == "down" else -1
            if self.wraps:
                self.selected = (self.selected + step) % len(self.menu)
            else:
                self.selected = max(0, min(len(self.menu) - 1, self.selected + step))

    def get_text(self):
        lines = ["...earlier output...",
                 "You've hit your session limit · resets 6:50pm (Asia/Taipei)"]
        if self.state == "menu":
            lines.append("What do you want to do?")
            for i, label in enumerate(self.menu):
                lines.append(("❯ " if i == self.selected else "  ") + label)
        if self.state == "confirmed":
            lines.append("Claude Code will continue automatically at 6:50pm. "
                         "Keep this session open; it may still pause for permission prompts.")
        # The input box stays drawn underneath, cursor glyph and all.
        lines += ["─" * 60, "❯ " + self.input, "─" * 60,
                  "  | ~/Projects/demo | Opus 5 |"]
        return "\n".join(lines)


def run(term):
    arm.get_text = lambda base, match: term.get_text()
    arm.send_text = lambda base, match, text: term.send_text(text)
    arm.send_key = lambda base, match, key: term.send_key(key)
    return arm.drive([], "id:1", CFG)


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def entry_below_the_highlight():
    """Pro's ordering: upsell and Stop come first, the wait sits third."""
    term = FakeTerm([UPGRADE, STOP, WAIT, LOWPRI], selected=0)
    assert run(term) == "armed"
    assert term.entered_on == WAIT, term.entered_on


@case
def entry_above_the_highlight_without_wrapping():
    """A list that stops at the bottom must be walked back upwards."""
    term = FakeTerm([WAIT, STOP, UPGRADE], selected=2, wraps=False)
    assert run(term) == "armed"
    assert term.entered_on == WAIT
    assert "up" in term.keys


@case
def entry_already_selected():
    term = FakeTerm([STOP, WAIT], selected=1)
    assert run(term) == "armed"
    # Straight to Enter: the command, its submit, and the selection.
    assert term.keys.count("down") == 0 and term.keys.count("up") == 0


@case
def already_armed_menu_is_left_alone():
    term = FakeTerm([STOP, CANCEL_WAIT], selected=0)
    assert run(term) == "already_armed"
    assert term.entered_on is None
    assert term.keys[-1] == "escape"


@case
def half_written_prompt_is_never_stomped():
    term = FakeTerm([STOP, WAIT], typed="draft I was still writing")
    assert run(term) == "input_busy"
    assert term.keys == [], term.keys
    assert term.input == "draft I was still writing"


@case
def dialog_that_never_opens_backs_out():
    term = FakeTerm([STOP, WAIT], opens=False)
    assert run(term) == "no_dialog"
    assert term.entered_on is None
    assert term.keys[-1] == "escape"


@case
def missing_entry_backs_out_without_pressing_enter():
    """No wait entry offered at all -- must not settle for another row."""
    term = FakeTerm([UPGRADE, STOP, LOWPRI], selected=0)
    assert run(term) == "no_dialog"
    assert term.entered_on is None


def main():
    failures = 0
    for fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        else:
            print("ok   %s" % fn.__name__)
    print("\n%d/%d passed" % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
