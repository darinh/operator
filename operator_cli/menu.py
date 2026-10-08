"""Keyboard screens. Keys, drawing, and verbs are injected.

A screen returns a status string for the screen the user came from, or a
``Leave`` when the tool should restore the terminal and run a verb for real.
Start an operator, Attach, and Start and attach are the leaves.
"""
from __future__ import annotations

import io
import shutil
import sys
import textwrap
from contextlib import redirect_stderr, redirect_stdout


class Row:
    def __init__(self, label: str, selectable: bool = True):
        self.label = label
        self.selectable = selectable


class Op:
    def __init__(self, name: str, cwd: str, label: str, running: bool):
        self.name = name
        self.cwd = cwd
        self.label = label
        self.running = running


class Leave:
    """Run ``call`` after the terminal is restored, then exit with its code."""

    def __init__(self, call):
        self.call = call


def render(title: str, rows, highlight=None, status: str = "") -> None:
    out = sys.stdout
    width = shutil.get_terminal_size().columns - 3
    out.write("\x1b[2J\x1b[H")
    if status:
        out.write(status.rstrip("\n"))
        out.write("\n\n")
    out.write(title)
    out.write("\n")
    for index, row in enumerate(rows):
        out.write("> " if index == highlight else "  ")
        out.write(row[:width])
        out.write("\n")
    out.flush()


def _room(title: str, status: str) -> int:
    """How many rows fit under the status and the title, with the cursor's line left free."""
    columns, lines = shutil.get_terminal_size()
    above = f"{status.rstrip(chr(10))}\n\n{title}" if status else title
    used = sum(len(line) // columns + 1 for line in above.split("\n"))
    return max(lines - used - 1, 3)


def select(title, rows, keys, render, status=""):
    """Index of the chosen row, or None on Esc. Headings are not chosen. A list
    longer than the terminal scrolls, keeping the row above the highlight in view."""
    labels = [row.label for row in rows]
    selectable = [i for i, row in enumerate(rows) if row.selectable]
    cursor = top = 0
    while True:
        room = _room(title, status)
        at = selectable[cursor] if selectable else 0
        top = max(min(top, at - 1), at - room + 1, 0)
        render(title, labels[top:top + room],
               highlight=at - top if selectable else None, status=status)
        key = next(keys)
        if key == "esc":
            return None
        if not selectable:
            continue
        if key == "up":
            cursor = (cursor - 1) % len(selectable)
        elif key == "down":
            cursor = (cursor + 1) % len(selectable)
        elif key == "enter":
            return selectable[cursor]


def page(title, text, keys, render) -> None:
    """Show ``text`` wrapped to the terminal until Enter or Esc. Up and Down scroll."""
    width = shutil.get_terminal_size().columns - 3
    lines = [part for line in text.split("\n")
             for part in textwrap.wrap(line, width, break_on_hyphens=False) or [""]]
    top = 0
    while True:
        room = _room(title, "")
        render(title, lines[top:top + room], highlight=None)
        key = next(keys)
        if key in ("enter", "esc"):
            return None
        if key == "up":
            top = max(top - 1, 0)
        elif key == "down":
            top = min(top + 1, max(len(lines) - room, 0))


def multi_select(title, labels, keys, render, status=""):
    """Names left toggled on, ``[]`` when Enter finds none, None on Esc. A list
    longer than the terminal scrolls, as in ``select``."""
    on = [False] * len(labels)
    cursor = top = 0
    if not labels:
        render(title, ["(none)"], highlight=None, status=status)
        next(keys)
        return []
    while True:
        room = _room(title, status)
        top = max(min(top, cursor - 1), cursor - room + 1, 0)
        shown = [f"[{'x' if on[i] else ' '}] {i + 1}. {labels[i]}"
                 for i in range(len(labels))]
        render(title, shown[top:top + room], highlight=cursor - top, status=status)
        key = next(keys)
        if key == "up":
            cursor = (cursor - 1) % len(labels)
        elif key == "down":
            cursor = (cursor + 1) % len(labels)
        elif key == "space":
            on[cursor] = not on[cursor]
        elif key == "enter":
            return [labels[i] for i in range(len(labels)) if on[i]]
        elif key == "esc":
            return None


def confirm(lines, keys, render) -> bool:
    """y or Y is yes. Any other key is no."""
    render(lines[0], list(lines[1:]), highlight=None)
    return next(keys) in ("y", "Y")


def ask_text(title, keys, render, prefill="", status="") -> "str | None":
    """The submitted text, or None on Esc."""
    buf = list(prefill)
    while True:
        render(title, ["".join(buf)], highlight=None, status=status)
        key = next(keys)
        if key == "enter":
            return "".join(buf)
        if key == "esc":
            return None
        if key == "backspace":
            if buf:
                del buf[-1]
        elif key == "space":
            buf.append(" ")
        elif len(key) == 1:
            buf.append(key)


def _captured(fn, argv, render) -> str:
    render("Working...", [], highlight=None)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv)
    text = (out.getvalue() + err.getvalue()).strip()
    if code and not text:
        return f"exited {code}"
    return text


def _list_rows(running, offline):
    rows = [Row("Running:", False)]
    found = {}
    if not running:
        rows.append(Row("(none)", False))
    for index, item in enumerate(running, 1):
        rows.append(Row(f"{index}. {item.label}"))
        found[len(rows) - 1] = item
    rows.append(Row("Offline:", False))
    if not offline:
        rows.append(Row("(none)", False))
    for index, item in enumerate(offline, 1):
        rows.append(Row(f"{index}. {item.label}"))
        found[len(rows) - 1] = item
    return rows, found


def _main(keys, render, actions, status):
    while True:
        count = actions.recoverable_count()
        recover = "No operators need recovery."
        if count:
            recover = f"Recover operator sessions ({count})"
        labels = ["Start an operator", "List operators",
                  f"Messaging ({actions.waiting_count()})", recover, "Quit"]
        picked = select("operator", [Row(label) for label in labels],
                        keys, render, status=status)
        if picked is None:
            return None
        if labels[picked] == "Quit":
            return None
        if labels[picked] == "No operators need recovery.":
            continue
        if labels[picked].startswith("Recover operator sessions"):
            return "recover"
        if labels[picked].startswith("Messaging"):
            return "messaging"
        if labels[picked] == "List operators":
            return "list"
        return "start"


def start_screen(keys, render, actions):
    title = (f"Start an operator in {actions.cwd()}\n"
             "Enter starts it and attaches this terminal. Esc goes back.\n"
             "Name:")
    prefill, status = actions.default_name(), ""
    while True:
        typed = ask_text(title, keys, render, prefill=prefill, status=status)
        if typed is None:
            return ""
        name = typed.strip()
        status = actions.start_problem(name)
        if status is None:
            return Leave(lambda: actions.start(["--name", name, "--attach"]))
        prefill = typed


def action_screen(item, keys, render, actions):
    if item.running:
        choices = ("Attach", "Stop")
    else:
        choices = ("Start", "Start and attach", "Rename", "Delete")
    while True:
        picked = select(item.name, [Row(choice) for choice in choices],
                        keys, render)
        if picked is None:
            return ""
        choice = choices[picked]
        if choice == "Attach":
            return Leave(lambda name=item.name: actions.attach([name]))
        if choice == "Start and attach":
            return Leave(lambda name=item.name:
                         actions.start(["--name", name, "--attach"]))
        if choice == "Stop":
            return _captured(actions.stop, [item.name], render)
        if choice == "Start":
            return _captured(actions.start, ["--name", item.name], render)
        if choice == "Rename":
            new = ask_text("Operator name:", keys, render, prefill=item.name)
            if new is None:
                continue
            return _captured(actions.rename, [item.name, new], render)
        lines = [
            f"Deletes operator {item.name} and all of its settings.",
            f"Repo: {item.cwd}",
            "Delete? [y/N]",
        ]
        if confirm(lines, keys, render):
            return _captured(actions.delete, [item.name, "--yes"], render)


def list_screen(keys, render, actions):
    status = ""
    while True:
        running, offline, problems = actions.sections()
        rows, found = _list_rows(running, offline)
        picked = select("Operators", rows, keys, render,
                        status="\n".join([*problems, status]))
        if picked is None:
            return ""
        done = action_screen(found[picked], keys, render, actions)
        if isinstance(done, Leave):
            return done
        status = done


def recover_screen(keys, render, actions) -> str:
    chosen = multi_select("Recover operator sessions",
                          actions.recoverable_names(), keys, render)
    if not chosen:
        return ""
    return _captured(actions.recover, chosen, render)


def messaging_screen(keys, render, actions) -> str:
    screens = {"Inbox": inbox_screen, "Send a message": send_screen,
               "Message Log": log_screen}
    choices = list(screens)
    status = ""
    while True:
        picked = select("Messaging", [Row(choice) for choice in choices],
                        keys, render, status=status)
        if picked is None:
            return ""
        status = screens[choices[picked]](keys, render, actions)


def inbox_screen(keys, render, actions) -> str:
    """The caller's new mail, filed as read, the same as `operator inbox`."""
    page("Inbox. Messages shown here are now marked read. Esc goes back.",
         _captured(actions.inbox, [], render), keys, render)
    return ""


def send_screen(keys, render, actions) -> str:
    """Pick an operator the caller may message, then type the message."""
    while True:
        running, offline, problems = actions.recipients()
        rows, found = _list_rows(running, offline)
        picked = select("Send a message. Esc goes back.",
                        rows, keys, render, status="\n".join(problems))
        if picked is None:
            return ""
        name = found[picked].name
        text = ask_text(f"Message to {name}. Enter sends it. Esc goes back.",
                        keys, render)
        if text is not None:
            return _captured(actions.send, [name, text], render)


def log_screen(keys, render, actions) -> str:
    """Every message in this home, oldest first. Enter opens one."""
    entries = actions.message_log()
    rows = [Row(f"{n}. {row}") for n, (row, _) in enumerate(entries, 1)]
    if not entries:
        rows.append(Row("(none)", False))
    while True:
        picked = select("Message Log, oldest first. Enter shows a whole message. "
                        "Esc goes back.", rows, keys, render)
        if picked is None:
            return ""
        page(f"Message {picked + 1}. Esc goes back.", entries[picked][1], keys, render)


def run(keys, render, actions):
    """0 when the user quits, or a ``Leave`` the caller runs outside raw mode."""
    status = ""
    while True:
        pick = _main(keys, render, actions, status)
        if pick is None:
            return 0
        screen = {"start": start_screen, "list": list_screen,
                  "recover": recover_screen, "messaging": messaging_screen}[pick]
        done = screen(keys, render, actions)
        if isinstance(done, Leave):
            return done
        status = done
