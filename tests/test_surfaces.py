"""The menu and the command line, held to the same outcomes.

A case names an outcome and the ways to reach it: argv lists, a menu path, or
both. Every way runs in a fresh sandbox and must leave that outcome, which is
the events the fakes saw, the operators and projects on disk, and what the
user was told. Where the menu differs on purpose, the case says how and quotes
the README line that documents it.

The meta-tests at the bottom fail when a verb, an option or a menu item has no
case, and when the README tables disagree with the cases. They find menu items
by walking the menu with fake verbs in four states. A choice none of those
states shows still escapes when no statement of its own guards it and the walk
sees its label elsewhere, as when an index computed from data picks it. A key
or any other branch inside the input loops that menu.py's screens are built
on escapes too, and so does code outside menu.py. An option escapes when it is
built from pieces or when code outside operator_cli parses it. One that
operator_cli imports from another package fails a test of its own when it sits
in a collection or an object's attributes, however deep. It escapes when it
exists only once code has run, sits anywhere else, such as in a closure, or is
reached by a name built when the code runs.

The README tests read the map table, the first column of each one-sided table
and the bullets under Where they behave differently. A one-sided row must give
a reason and each bullet must be a case's doc, but no test can tell whether
either is true. The prose under Verbs is not read.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import shutil
import sys
from collections import deque
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from functools import cache
from itertools import takewhile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import op
from operator_cli import entry as cli
from operator_cli import menu
from test_entry import FakeTTY
from test_handoff import _seat
from test_menu import Actions as MenuActions

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "operator_cli"
README = REPO / "README.md"

#: Why a menu run that stays open compares no exit code. README.md says so too.
STAYS = ("A choice that keeps the menu open shows the command's message on "
         "the screen and has no exit code. The typed command prints the message "
         "and exits non-zero when it fails.")


# ── driving the menu ────────────────────────────────────────────


@dataclass(frozen=True)
class Key:
    """Press one key."""
    name: str


@dataclass(frozen=True)
class Text:
    """Clear the text box, type ``value`` and press Enter."""
    value: str


@dataclass(frozen=True)
class Toggle:
    """Move to the row showing ``label`` and press Space."""
    label: str


ENTER = Key("enter")


@dataclass
class Frame:
    title: str
    rows: list
    highlight: "int | None"
    status: str


_MARKS = re.compile(r"^(\[[ x]\] )?(\d+\. )?")
#: The one row whose text carries a count. It is one choice whatever the count.
COUNTED = "Recover operator sessions"


def _choice(label: str) -> str:
    """The choice a row offers: its whole text, less the count on COUNTED."""
    stem = re.sub(r" \(\d+\)$", "", label)
    return stem if stem == COUNTED else label


def _shows(row: str, label: str) -> bool:
    """Whether ``row`` is the row for ``label``, ignoring its number and details."""
    bare = _MARKS.sub("", row, count=1)
    return _choice(bare) == label or bare.startswith(label + "  (")


class Robot:
    """Keys for a menu path, chosen by reading the screens the menu draws.

    A str step moves to the row showing it and presses Enter. After the path,
    ``landed`` is the screen the menu stopped on, or None when the menu left.
    """

    def __init__(self, path, then=()):
        self.path, self.then = tuple(path), tuple(then)
        self.frames: list[Frame] = []
        self.landed: "Frame | None" = None
        self.landed_at = 0

    def render(self, title, rows, highlight=None, status=""):
        self.frames.append(Frame(title, list(rows), highlight, status))

    def keys(self):
        for step in self.path:
            if isinstance(step, Key):
                yield step.name
            elif isinstance(step, Text):
                yield from self._type(step.value)
            elif isinstance(step, Toggle):
                yield from self._move_to(step.label)
                yield "space"
            else:
                yield from self._move_to(step)
                yield "enter"
        self.landed_at = len(self.frames) - 1
        self.landed = self.frames[-1]
        yield from self.then
        for _ in range(50):
            yield "esc"
        raise AssertionError("the menu would not close")

    def _move_to(self, label):
        for _ in range(len(self.frames[-1].rows) + 1):
            frame = self.frames[-1]
            if frame.highlight is not None and _shows(frame.rows[frame.highlight], label):
                return
            yield "down"
        frame = self.frames[-1]
        raise AssertionError(f"no {label!r} on {frame.title!r}: {frame.rows}")

    def _type(self, value):
        frame = self.frames[-1]
        assert len(frame.rows) == 1 and frame.highlight is None, (
            f"no text box on {frame.title!r}")
        yield from ["backspace"] * len(frame.rows[0])
        yield from ("space" if char == " " else char for char in value)
        yield "enter"


# ── the cases ───────────────────────────────────────────────────


@dataclass
class Case:
    """One outcome and the ways to reach it.

    ``menu`` None means the command line only, and ``()`` means the main menu
    as it opens. ``leaves`` means the menu hands this terminal over or ends,
    rather than staying open. ``answer`` "screen" compares the lines shown
    rather than the message. ``menu_expect`` overrides ``expect`` for the
    menu, only where the README documents the difference, and ``doc`` is the
    README bullet that documents it, word for word. ``stands`` is the place
    the user runs operator in.
    """
    id: str
    expect: dict = field(default_factory=dict)
    argv: tuple = ()
    menu: "tuple | None" = None
    leaves: bool = False
    code: int = 0
    given: dict = field(default_factory=dict)
    recoverable: tuple = ()
    answer: str = "said"
    seated: str = ""
    menu_expect: dict = field(default_factory=dict)
    doc: str = ""
    stands: str = "here"

    def expected(self, surface: str) -> dict:
        want = {
            "events": [],
            "operators": sorted((name, where) for name, (where, _) in self.given.items()),
            "registered": sorted({where for where, _ in self.given.values()}),
            "said": "",
            "waited": 0.0,
        }
        want.update(self.expect)
        if surface == "menu":
            want.update(self.menu_expect)
        return want


def spawned(name, where="here", args=(), fresh=False, home="home"):
    return ("spawn", name, where, list(args), fresh, home)


def attached(name):
    return ("attach", name)


BUSY = {"alpha": ("here", "running"), "bravo": ("there", "offline")}
IDLE_HERE = {"alpha": ("here", "offline")}
REGISTERED = "registered this directory as a project (<guid>)"
START_USAGE = ("Usage: operator start [NAME] [--name NAME] [--agent AGENT] "
               "[--attach] [--fresh] [task...]")
DELETE_USAGE = "Usage: operator delete NAME [--yes]"
HANDOFF_USAGE = ("Usage: operator handoff --status TEXT [--next TEXT] "
                 "[--context TEXT] [--instance NAME] [--no-restart]")
RECOVER_HELP = """\
usage: operator recover [-h] [--all] [--home HOME] [name ...]

Restart the operators that were supervised when this machine stopped,
continuing each where it left off.

positional arguments:
  name         operators to recover (default: list them)

options:
  -h, --help   show this help message and exit
  --all        recover every operator that needs it
  --home HOME  operator state directory (default: ~/.operator)"""
HELP = """\
Usage: operator [command]
No command opens a keyboard menu when stdin and stdout are a TTY.

  doctor                check that this machine can run operator
  start                 start a supervised operator (start [NAME] [TASK])
  list                  list operators
  attach                attach this terminal to a running operator
  stop                  ask an operator's supervisor to stop
  rename                rename an operator
  delete                delete an operator and its settings
  recover               list operators that need recovering after a crash
  handoff               write this operator's handoff and start the next session"""
WRITTEN = "handoff written to <home>/projects/<guid>/handoff/<id>.md"
RECOVERING = "Recovering 'alpha' in <here>"
TAKEN = ("Start an operator refuses a name that an operator in another directory "
         "has, and asks again. `operator start NAME` starts that operator in its "
         "own directory. `operator start` with no name exits 2 and asks for a "
         "name when that operator has this directory's name.")
SEVERAL = ("With several operators in this directory, Start an operator leaves "
           "the name empty and asks for one. `operator start` with no name lists "
           "them and exits 2.")
LIST_EMPTY = ("`operator list` with no operators says \"No operators yet. Start one "
              "with: operator start\". List operators shows `(none)` under both "
              "headings.")
RECOVER_LISTS = ("`operator recover` with no names lists the operators that need "
                 "recovering, or says none do. The menu shows how many on its main "
                 "menu row.")
LIST_SCREEN = ("The list screen offers Attach and Stop for a running operator, and "
               "Start, Start and attach, Rename and Delete for a stopped one. For a "
               "running operator, `operator rename` renames it, `operator start NAME "
               "--attach` attaches, and `operator start NAME` and `operator delete` "
               "refuse. For a stopped operator, `operator attach` refuses and "
               "`operator stop` prints \"stop requested for NAME\" with nothing to stop.")
RUNNING_ROWS = "Attach\nStop"
STOPPED_ROWS = "Start\nStart and attach\nRename\nDelete"

CASES = [
    Case("start-here-new",
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"events": [spawned("demo"), attached("demo")],
                 "operators": [("demo", "here")], "registered": ["here"],
                 "said": REGISTERED + "\nstarted demo (pid 41)", "waited": 0.05}),
    Case("start-typed-name",
         argv=(["start", "--name", "new", "--attach"],
               ["start", "new", "--attach"],
               ["start", "--name=new", "--attach"]),
         menu=("Start an operator", Text("new")), leaves=True,
         expect={"events": [spawned("new"), attached("new")],
                 "operators": [("new", "here")], "registered": ["here"],
                 "said": REGISTERED + "\nstarted new (pid 41)", "waited": 0.05}),
    Case("start-here-existing", given=IDLE_HERE,
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"events": [spawned("alpha"), attached("alpha")],
                 "said": "started alpha (pid 41)", "waited": 0.05}),
    Case("start-existing-spaces", given=IDLE_HERE,
         argv=(["start", " alpha ", "--attach"],
               ["start", "--name", " alpha ", "--attach"]),
         menu=("Start an operator", Text(" alpha ")), leaves=True,
         expect={"events": [spawned("alpha"), attached("alpha")],
                 "said": "started alpha (pid 41)", "waited": 0.05}),
    Case("start-here-running", given={"alpha": ("here", "running")},
         argv=(["start", "--attach"], ["start", "alpha", "--attach"]),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"events": [attached("alpha")],
                 "said": "alpha is already running"}),
    Case("start-running-asks-more", given={"alpha": ("here", "running")}, code=1,
         argv=(["start", "alpha", "--attach", "--fresh"],
               ["start", "alpha", "--attach", "fix", "it"],
               ["start", "alpha", "--attach", "--model", "gpt"]),
         expect={"said": "alpha is already running"}),
    Case("start-here-between", given={"alpha": ("here", "between")}, code=1,
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"said": "alpha is already running\nalpha has no session to "
                         "attach to yet. Try again: operator attach alpha",
                 "waited": 2.0}),
    Case("start-here-starting", given={"alpha": ("here", "starting")},
         argv=(["start", "--attach"], ["start", "alpha", "--attach"]),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"events": [attached("alpha")],
                 "said": "alpha is already running", "waited": 0.05}),
    Case("start-here-slow", given={"alpha": ("here", "slow")}, code=1,
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER), leaves=True,
         expect={"events": [spawned("alpha")],
                 "said": "started alpha (pid 41)\nalpha has no session to "
                         "attach to yet. Try again: operator attach alpha",
                 "waited": 2.0}),
    Case("start-several-here", code=2,
         given={"alpha": ("here", "offline"), "bravo": ("here", "running")},
         argv=(["start", "--attach"], ["start"]),
         menu=("Start an operator", ENTER),
         expect={"said": "2 operators work here: alpha, bravo\n"
                         "pass a name: operator start NAME"},
         menu_expect={"said": "a name is needed"},
         doc=SEVERAL),
    Case("start-blank-name", code=2,
         argv=(["start", "", "--attach"], ["start", " "]),
         menu=("Start an operator", Text("")),
         expect={"said": "a name is needed"}),
    Case("start-name-needs-value", code=2,
         argv=(["start", "--name"], ["start", "--name=", "--attach"],
               ["start", "--name", " ", "--attach"]),
         expect={"said": "operator start --name needs a value"}),
    Case("start-bad-name", code=2,
         argv=(["start", "--name=-x", "--attach"],),
         menu=("Start an operator", Text("-x")),
         expect={"said": "a name cannot start with -"}),
    Case("start-here-bad-name", stands="dashed", code=2,
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER),
         expect={"said": "a name cannot start with -"}),
    Case("start-taken-elsewhere", given={"bravo": ("there", "offline")},
         argv=(["start", "bravo", "--attach"],),
         menu=("Start an operator", Text("bravo")),
         expect={"events": [spawned("bravo", "there"), attached("bravo")],
                 "said": "started bravo (pid 41)", "waited": 0.05},
         menu_expect={"events": [], "waited": 0.0,
                      "said": "bravo already works in <there>. Choose another name."},
         doc=TAKEN),
    Case("start-here-taken-elsewhere", given={"demo": ("there", "offline")}, code=2,
         argv=(["start", "--attach"],),
         menu=("Start an operator", ENTER),
         expect={"said": "an operator named 'demo' already works in <there>\n"
                         "pass a name: operator start --name NAME"},
         menu_expect={"said": "demo already works in <there>. Choose another name."},
         doc=TAKEN),
    Case("list", given=BUSY, answer="screen",
         argv=(["list"],),
         menu=("List operators",),
         expect={"said": "Running:\n1. alpha  (<here>)\nOffline:\n1. bravo  (<there>)"}),
    Case("list-empty", answer="screen",
         argv=(["list"],),
         menu=("List operators",),
         expect={"said": "No operators yet. Start one with: operator start"},
         menu_expect={"said": "Running:\n(none)\nOffline:\n(none)"},
         doc=LIST_EMPTY),
    Case("attach", given=BUSY,
         argv=(["attach", "alpha"],),
         menu=("List operators", "alpha", "Attach"), leaves=True,
         expect={"events": [attached("alpha")]}),
    Case("stop", given=BUSY,
         argv=(["stop", "alpha"],),
         menu=("List operators", "alpha", "Stop"),
         expect={"events": [("stop", "alpha")], "said": "stop requested for alpha"}),
    Case("start-offline", given=BUSY,
         argv=(["start", "bravo"], ["start", "--name", "bravo"]),
         menu=("List operators", "bravo", "Start"),
         expect={"events": [spawned("bravo", "there")],
                 "said": "started bravo (pid 41)"}),
    Case("start-and-attach", given=BUSY,
         argv=(["start", "bravo", "--attach"],),
         menu=("List operators", "bravo", "Start and attach"), leaves=True,
         expect={"events": [spawned("bravo", "there"), attached("bravo")],
                 "said": "started bravo (pid 41)", "waited": 0.05}),
    Case("rename", given=BUSY,
         argv=(["rename", "bravo", "charlie"],),
         menu=("List operators", "bravo", "Rename", Text("charlie")),
         expect={"operators": [("alpha", "here"), ("charlie", "there")],
                 "said": "renamed bravo to charlie"}),
    Case("delete", given=BUSY,
         argv=(["delete", "bravo", "--yes"],),
         menu=("List operators", "bravo", "Delete", Key("y")),
         expect={"operators": [("alpha", "here")], "registered": ["here"],
                 "said": "deleted bravo"}),
    Case("rename-running", given=BUSY, answer="screen",
         argv=(["rename", "alpha", "zed"],),
         menu=("List operators", "alpha"),
         expect={"operators": [("bravo", "there"), ("zed", "here")],
                 "said": "renamed alpha to zed"},
         menu_expect={"operators": [("alpha", "here"), ("bravo", "there")],
                      "said": RUNNING_ROWS},
         doc=LIST_SCREEN),
    Case("start-running", given=BUSY, code=1, answer="screen",
         argv=(["start", "alpha"],),
         menu=("List operators", "alpha"),
         expect={"said": "alpha is already running"},
         menu_expect={"said": RUNNING_ROWS},
         doc=LIST_SCREEN),
    Case("delete-running", given=BUSY, code=1, answer="screen",
         argv=(["delete", "alpha", "--yes"],),
         menu=("List operators", "alpha"),
         expect={"said": "stop it first: operator stop alpha"},
         menu_expect={"said": RUNNING_ROWS},
         doc=LIST_SCREEN),
    Case("attach-stopped", given=BUSY, code=1, answer="screen",
         argv=(["attach", "bravo"],),
         menu=("List operators", "bravo"),
         expect={"said": "No running operator 'bravo'."},
         menu_expect={"said": STOPPED_ROWS},
         doc=LIST_SCREEN),
    Case("stop-stopped", given=BUSY, answer="screen",
         argv=(["stop", "bravo"],),
         menu=("List operators", "bravo"),
         expect={"events": [("stop", "bravo")], "said": "stop requested for bravo"},
         menu_expect={"events": [], "said": STOPPED_ROWS},
         doc=LIST_SCREEN),
    Case("recover", given=IDLE_HERE, recoverable=("alpha",),
         argv=(["recover", "alpha"],),
         menu=("Recover operator sessions", Toggle("alpha"), ENTER),
         expect={"events": [spawned("alpha")], "said": RECOVERING}),
    Case("recover-then-none", given=IDLE_HERE, recoverable=("alpha",),
         menu=("Recover operator sessions", Toggle("alpha"), ENTER,
               "No operators need recovery."),
         expect={"events": [spawned("alpha")], "said": RECOVERING}),
    Case("recover-none", answer="screen",
         argv=(["recover"],),
         menu=("No operators need recovery.",),
         expect={"said": "No operators need recovering."},
         menu_expect={"said": "Start an operator\nList operators\n"
                              "No operators need recovery.\nQuit"},
         doc=RECOVER_LISTS),
    Case("recover-listing", given=IDLE_HERE, recoverable=("alpha",), answer="screen",
         argv=(["recover"],),
         menu=(),
         expect={"said": "1 operator(s) were running when this machine last stopped:\n"
                         "alpha\n\n"
                         "Bring them all back with: operator recover --all\n"
                         "Or one at a time with:    operator recover <name>"},
         menu_expect={"said": "Start an operator\nList operators\n"
                              "Recover operator sessions (1)\nQuit"},
         doc=RECOVER_LISTS),
    Case("quit", menu=("Quit",), leaves=True),
    Case("start-fresh", given=IDLE_HERE,
         argv=(["start", "--fresh"], ["start", "alpha", "--fresh"]),
         expect={"events": [spawned("alpha", fresh=True)],
                 "said": "started alpha (pid 41)"}),
    Case("start-agent-task",
         argv=(["start", "alpha", "--agent", "coder", "fix", "the", "build"],
               ["start", "--name", "alpha", "--agent=coder", "fix", "the", "build"]),
         expect={"events": [spawned("alpha", args=["--agent", "coder", "--",
                                                   "fix", "the", "build"])],
                 "operators": [("alpha", "here")], "registered": ["here"],
                 "said": REGISTERED + "\nstarted alpha (pid 41)"}),
    Case("start-help",
         argv=(["start", "--help"], ["start", "-h"]),
         expect={"said": START_USAGE}),
    Case("delete-help",
         argv=(["delete", "--help"], ["delete", "-h"]),
         expect={"said": DELETE_USAGE}),
    Case("help",
         argv=(["--help"], ["-h"], ["help"]),
         expect={"said": HELP}),
    Case("home", given=IDLE_HERE,
         argv=(["--home", "<other-home>", "start", "alpha"],
               ["--home=<other-home>", "start", "alpha"]),
         expect={"events": [spawned("alpha", home="other-home")],
                 "said": "started alpha (pid 41)"}),
    Case("recover-all", given=IDLE_HERE, recoverable=("alpha",),
         argv=(["recover", "--all"],),
         expect={"events": [spawned("alpha")],
                 "said": RECOVERING + "\nRecovered 1 of 1 operator(s)."}),
    Case("recover-help",
         argv=(["recover", "--help"], ["recover", "-h"]),
         expect={"said": RECOVER_HELP}),
    Case("doctor",
         argv=(["doctor"],),
         expect={"said": "copilot: /bin/copilot\nmultiplexer: fakemux\n"
                         "home: writable at <home>\ndoctor: ok"}),
    Case("handoff", given=IDLE_HERE, seated="alpha",
         argv=(["handoff", "--status", "done", "--next", "test",
                "--context", "none", "--instance", "alpha"],),
         expect={"said": WRITTEN + "\nrestart requested for <id>"}),
    Case("handoff-no-restart", given=IDLE_HERE, seated="alpha",
         argv=(["handoff", "alpha", "--status=done", "--no-restart"],),
         expect={"said": WRITTEN}),
    Case("handoff-help",
         argv=(["handoff", "--help"], ["handoff", "-h"]),
         expect={"said": HANDOFF_USAGE}),
]


# ── the world each way runs in ──────────────────────────────────


class World:
    """A sandbox, with fakes wherever operator would reach the machine.

    Places have names so outcomes read the same on every machine. ``here`` is
    the directory the user stands in, ``there`` is another project, ``dashed``
    is a directory whose name is not a valid operator name, ``home`` is the
    operator home and ``other-home`` is the one ``--home`` names.
    """

    def __init__(self, tmp_path, monkeypatch, capsys, home):
        import operators
        import supervisor
        import supervisor_control
        self.monkeypatch, self.capsys = monkeypatch, capsys
        self.places = {"here": tmp_path / "demo", "there": tmp_path / "elsewhere",
                       "dashed": tmp_path / "-dashed",
                       "other-home": tmp_path / "other-home", "home": home}
        for name in ("here", "there", "dashed", "other-home"):
            self.places[name].mkdir()
        monkeypatch.chdir(self.places["here"])
        # argparse wraps recover's help to the terminal's width.
        monkeypatch.setenv("COLUMNS", "80")
        self.events: list = []
        self.between: set = set()
        self.starting: list = []
        self.slow: set = set()
        self.waited_ms = 0

        def sleep(seconds):
            self.waited_ms += round(seconds * 1000)
            while self.starting:
                self._run(*self.starting.pop())

        def spawn(instance, copilot_args, is_fresh, cwd=None):
            self.events.append(spawned(
                instance.display_name, self.place(cwd), copilot_args, is_fresh,
                self.place(os.environ["COPILOT_OPERATOR_HOME"])))
            # A real supervisor publishes its pid before it opens its session.
            self.between.add(instance.id)
            if instance.id not in self.slow:
                self.starting.append((instance.session, cwd))
            return 41

        def stop(instance, *_):
            self.events.append(("stop", instance.display_name))
            op.MUX.sessions.pop(instance.session, None)

        def attach(session):
            self.events.append(attached(operators.find(session).name))
            return 0

        which = shutil.which
        loop_pid = supervisor_control._running_loop_pid
        # Recover spawns through supervisor_control's own binding.
        for module in (supervisor, supervisor_control):
            monkeypatch.setattr(module, "_spawn_background_loop", spawn)
        monkeypatch.setattr(supervisor_control, "launch_status",
                            lambda inst, pid, **k: ("ready", pid))
        # A supervisor between sessions is alive and has no session. A starting
        # one, or one start spawns, gets its session the first time the clock
        # moves, unless its operator is slow.
        monkeypatch.setattr(supervisor_control, "_running_loop_pid", lambda inst: (
            43 if inst.id in self.between else loop_pid(inst)))
        # The session wait runs whole on a clock that only sleeping moves, so a
        # case says how long the user waited. Only this module's name changes.
        monkeypatch.setattr(supervisor_control, "time", SimpleNamespace(
            monotonic=lambda: self.waited_ms / 1000, sleep=sleep))
        monkeypatch.setattr(supervisor_control, "_request_supervisor_stop", stop)
        monkeypatch.setattr(op.MUX, "attach", attach)
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: (
            "/bin/copilot" if name == "copilot" else which(name, *a, **k)))

    @staticmethod
    def _run(session, cwd):
        op.MUX.sessions[session] = {"cwd": str(cwd), "argv": [],
                                    "remain_on_exit": False, "dead": False}

    def give(self, case: Case) -> None:
        import operators
        from operator_cli import project
        for name, (where, state) in case.given.items():
            assert state in ("offline", "running", "between", "starting", "slow"), state
            directory = self.places[where]
            assert project.ensure_registered(str(directory))[0] == 0
            record = operators.create(name, directory)
            if state == "running":
                self._run(record.id, directory)
            elif state in ("between", "starting"):
                self.between.add(record.id)
            if state == "starting":
                self.starting.append((record.id, directory))
            elif state == "slow":
                self.slow.add(record.id)
            if name in case.recoverable:
                # How its supervisor was started, which a crash leaves behind.
                loop_args = record.instance().loop_args_file
                loop_args.parent.mkdir(parents=True, exist_ok=True)
                loop_args.write_text(json.dumps({"user_args": [], "cwd": str(directory)}),
                                     encoding="utf-8")
        if case.seated:
            _seat(self.monkeypatch, operators.find(case.seated))
        self.monkeypatch.chdir(self.places[case.stands])
        self.capsys.readouterr()

    def typed(self, argv):
        filled = [token.replace("<other-home>", str(self.places["other-home"]))
                  for token in argv]
        code = cli.main(filled)
        seen = self.capsys.readouterr()
        return code, seen.out + seen.err

    def drive(self, path):
        robot = Robot(path)

        @contextmanager
        def raw_keys():
            yield robot.keys()

        self.monkeypatch.setattr(sys, "stdin", FakeTTY())
        self.monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        self.monkeypatch.setattr("operator_cli.keys.raw_keys", raw_keys)
        self.monkeypatch.setattr(menu, "render", robot.render)
        code = cli.main([])
        seen = self.capsys.readouterr()
        return robot, code, seen.out + seen.err

    def outcome(self, said: str) -> dict:
        import operators
        import paths
        self.monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(self.places["home"]))
        return {
            "events": self.events,
            "operators": sorted((record.name, self.place(record.cwd))
                                for record in operators.all_operators() or []),
            "registered": sorted(where for where in ("here", "there", "dashed")
                                 if paths.catalog_guid(self.places[where]).guid),
            "said": self.scrub(said),
            "waited": self.waited_ms / 1000,
        }

    def scrub(self, text: str) -> str:
        spellings = sorted(((str(spelling), f"<{name}>")
                            for name, path in self.places.items()
                            for spelling in (path, path.resolve())),
                           key=lambda pair: -len(pair[0]))
        for spelling, token in spellings:
            text = text.replace(spelling, token)
        text = re.sub(r"\bop-[0-9a-f]{8}\b", "<id>", text)
        text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}"
                      r"-[0-9a-f]{12}\b", "<guid>", text)
        return text.replace("\\", "/")

    def place(self, path) -> "str | None":
        if path is None:
            return None
        found = Path(path).resolve()
        for name, candidate in self.places.items():
            if candidate.resolve() == found:
                return name
        return str(path)


@pytest.fixture
def world(tmp_path, monkeypatch, capsys, _no_real_operator_home):
    return World(tmp_path, monkeypatch, capsys, _no_real_operator_home)


def _ways(cases):
    for case in cases:
        for index, argv in enumerate(case.argv):
            yield pytest.param(case, argv, id=f"{case.id}-argv{index}")
        if case.menu is not None:
            yield pytest.param(case, None, id=f"{case.id}-menu")


def _screen(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines())


@pytest.mark.parametrize("case, argv", list(_ways(CASES)))
def test_every_way_leaves_its_cases_outcome(world, case, argv):
    world.give(case)
    if argv is not None:
        code, text = world.typed(argv)
        assert code == case.code, text
        said = _screen(text) if case.answer == "screen" else text.strip()
        surface = "argv"
    else:
        robot, code, text = world.drive(case.menu)
        surface = "menu"
        assert (robot.landed is None) == case.leaves, (
            "the menu left" if robot.landed is None else "the menu stayed open")
        if robot.landed is None:
            assert code == case.code, text
            said = text.strip()
        else:
            # The menu stayed open, so there is no exit code to compare. STAYS.
            assert text == ""
            landed = robot.landed
            said = "\n".join(landed.rows) if case.answer == "screen" else landed.status
    assert world.outcome(said) == case.expected(surface)


def test_start_an_operator_in_a_new_directory_starts_attaches_and_lists_it(
        world, capsys):
    """The report this file exists for: Start an operator, then Enter."""
    robot, code, _ = world.drive(("Start an operator", ENTER))
    assert robot.landed is None
    assert code == 0
    assert world.events == [spawned("demo"), attached("demo")]
    assert world.waited_ms == 50
    assert cli.main(["list"]) == 0
    assert world.scrub(capsys.readouterr().out) == (
        "Running:\n  1. demo  (<here>)\nOffline:\n  (none)\n")


# ── nothing left behind ─────────────────────────────────────────

#: -h, --name=, -? and /? are options. -, -- and /dev/null are not.
OPTION = re.compile(r"^(?:--?|/)(?!-)[^\s=/]+=?$")
#: Their option literals are not typed by a person. menu.py builds argv for the
#: verbs, and supervise.py parses what `operator start` hands its child.
NOT_TYPED = ("supervise.py", "menu.py")
DATA_ROW = re.compile(r"^(?:\[[ x]\] )?\d+\. (.+?)(?:  \(.*)?$")
#: menu.py's input loops. tests/test_menu.py drives their keys; the walk need
#: not run every line of them, only every line of the screens built on them.
PRIMITIVES = ("render", "select", "multi_select", "confirm", "ask_text", "_captured")
#: The most paths the walk may take in one state. The many state takes 35.
WALK_LIMIT = 100
#: How a path names an operator: by the section of the list it is in, or, when
#: a box is ticked for it, as a name. In a command, each operator the path
#: picked is NAME.
RUNNING, OFFLINE, NAME = "<running>", "<offline>", "<name>"
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
#: The verbs whose handlers live in entry.py, by function name.
VERB_OF = {fn.__name__: verb for verb, fn in cli.HANDLERS.items()
           if fn.__module__ == cli.__name__}


def _verb(argv) -> "str | None":
    rest = cli._peel_home(list(argv))[1]
    return rest[0] if rest and rest[0] in cli.HANDLERS else None


def _words(cases) -> set:
    """The first word each argv hands the front door once --home is gone."""
    return {word for case in cases for argv in case.argv
            for word in cli._peel_home(list(argv))[1][:1]}


def _front_words() -> set:
    return set(cli.HANDLERS) | {word for word in cli.HELP_WORDS if not OPTION.match(word)}


def _node_name(node) -> "str | None":
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _peeled(spelling: str) -> bool:
    """Whether the front door takes ``spelling`` before any verb can see it."""
    tail = [spelling + "x"] if spelling.endswith("=") else [spelling, "x"]
    return cli._peel_home(["list", *tail])[0] == "x"


def _owner(path: Path, node, spelling: str) -> "str | None":
    """The verb that parses an option, or None for the front door."""
    if _peeled(spelling):
        return None
    if path.name == "entry.py":
        return VERB_OF.get(_node_name(node))
    if _node_name(node) in cli.HANDLERS:
        return _node_name(node)
    assert path.stem in cli.HANDLERS, (
        f"{path.name} has an option literal outside any verb. Move it into the "
        f"verb that parses it, or add the file to NOT_TYPED with a reason.")
    return path.stem


def _is_parser(call) -> bool:
    return isinstance(call, ast.Call) and getattr(
        call.func, "attr", getattr(call.func, "id", None)) == "ArgumentParser"


def _parser_help(call) -> tuple:
    """argparse gives every parser -h and --help unless add_help=False."""
    if not _is_parser(call) or any(
            word.arg == "add_help" and getattr(word.value, "value", True) is False
            for word in call.keywords):
        return ()
    return ("-h", "--help")


def _typed_options() -> set:
    """(verb, spelling) for every option operator_cli accepts.

    That is each option literal, and the help argparse adds to each parser.
    ``--name=`` is a spelling of its own because it has a branch of its own.
    An option built from pieces, or parsed outside operator_cli, escapes this
    scan. test_no_option_comes_from_another_package looks for one that
    operator_cli imports from another package.
    """
    found = set()
    for path in sorted(CLI.glob("*.py")):
        if path.name in NOT_TYPED:
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            for inner in ast.walk(node):
                spellings = _parser_help(inner)
                if (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                        and OPTION.match(inner.value)):
                    spellings = (inner.value,)
                found.update((_owner(path, node, spelling), spelling)
                             for spelling in spellings)
    return found


def _typed_by_cases(cases) -> set:
    """(verb, spelling) for every option the cases type, owned as above."""
    pairs = set()
    for case in cases:
        for argv in case.argv:
            verb = _verb(argv)
            for token in argv:
                if token == "--":
                    break
                spelling = token[:token.index("=") + 1] if "=" in token else token
                if OPTION.match(spelling):
                    pairs.add((None if _peeled(spelling) else verb, spelling))
    return pairs


def _ours(origin) -> bool:
    """Whether a module's file is this repository's code outside operator_cli."""
    if not origin:
        return False
    path = Path(origin).resolve()
    return path.is_relative_to(REPO) and not path.is_relative_to(CLI)


def _module(name: str) -> "ModuleType | None":
    """The module ``name`` if it is ours. Nothing else is imported, so a module
    that exists on one platform only is never loaded on another."""
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.has_location or not _ours(spec.origin):
        return None
    return importlib.import_module(name)


def _from_import(module: str, name: str):
    """What ``from module import name`` binds, if module is ours."""
    source = _module(module)
    if source is not None and not hasattr(source, name) and hasattr(source, "__path__"):
        return _module(f"{module}.{name}")
    return getattr(source, name, None)


def _read(node, bound):
    """What a name, an attribute chain such as ``a.b.C``, or ``getattr(a, "b")``
    reads, when every module along it is ours. A name built when the code runs
    is not read."""
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    outer = name = None
    if isinstance(node, ast.Attribute):
        outer, name = _read(node.value, bound), node.attr
    elif (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr"
          and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
          and isinstance(node.args[1].value, str)):
        outer, name = _read(node.args[0], bound), node.args[1].value
    if isinstance(outer, ModuleType) and _ours(getattr(outer, "__file__", None)):
        return getattr(outer, name, None)
    return None


def _options_in(value, seen=None) -> list:
    """The option spellings anywhere in ``value``.

    A str is one or is not. A dict, a tuple, a list or a set is searched
    through what it holds. Any other object is searched through the attributes
    in its ``__dict__``, and in those of its class and the class's bases that
    are ours. A module is not searched: operator_cli reads only the names it
    imports from one, and each of those is searched.
    """
    seen = set() if seen is None else seen
    if isinstance(value, str):
        return [str(value)] if OPTION.match(value) else []
    if isinstance(value, ModuleType) or id(value) in seen:
        return []
    seen.add(id(value))
    if isinstance(value, dict):
        held = [*value, *value.values()]
    elif isinstance(value, (tuple, list, set, frozenset)):
        held = list(value)
    else:
        mro = value.__mro__ if isinstance(value, type) else type(value).__mro__
        owners = [cls for cls in mro
                  if _ours(getattr(sys.modules.get(cls.__module__), "__file__", None))]
        if not isinstance(value, type):
            owners.append(value)
        held = [item for owner in owners
                for item in getattr(owner, "__dict__", {}).values()]
    return sorted(option for item in held for option in _options_in(item, seen))


def _labels(path):
    for step in path or ():
        if isinstance(step, str):
            yield step
        elif isinstance(step, Toggle):
            yield step.label


def _op(name: str, running: bool) -> menu.Op:
    where = "C:\\" + name[0]
    return menu.Op(name, where, f"{name}  ({where})", running)


def _busy() -> MenuActions:
    actions = MenuActions()
    actions.running, actions.offline = [_op("alpha", True)], [_op("bravo", False)]
    actions.recoverable = ["charlie"]
    return actions


def _many() -> MenuActions:
    """Two of each kind, so a choice that needs more than one shows, and a path
    runs for two operators in one state."""
    actions = MenuActions()
    actions.running = [_op("alpha", True), _op("delta", True)]
    actions.offline = [_op("bravo", False), _op("echo", False)]
    actions.recoverable = ["charlie", "foxtrot"]
    return actions


def _refusing() -> MenuActions:
    actions = MenuActions()
    actions.problems = {"demo": "taken"}
    return actions


def _careless() -> MenuActions:
    """The many state, but Stop and Rename act on the first operator of their
    section, whichever one they were given."""
    actions = _many()
    stop, rename = actions.stop, actions.rename
    actions.stop = lambda argv: stop([actions.running[0].name])
    actions.rename = lambda argv: rename([actions.offline[0].name, argv[1]])
    return actions


@dataclass(frozen=True)
class Walk:
    items: frozenset
    ran: frozenset
    shown: frozenset
    passed: frozenset
    returned: frozenset
    commands: frozenset

    @property
    def labels(self) -> frozenset:
        return frozenset(path[-1] for _, path in self.items)


@cache
def _walk(states=(("idle", MenuActions), ("busy", _busy), ("many", _many),
                  ("refusing", _refusing))) -> Walk:
    """Every choice the menu offers, as the state and the path it was found in,
    and what the walk saw on the way.

    By default the walk drives fake verbs in four states: no operators, one of
    each kind, two of each kind, and a start that refuses the name. It takes
    shorter paths first. It surveys each screen it lands on by pressing Down
    until the highlight has visited every row, and it enters every row the
    first time it lands on that screen in a state. When it lands there again,
    after a verb ran, it enters only a row it has not entered there before, and
    that choice counts under the path that reached it this time. A numbered row
    is an operator, known by its kind: running, stopped, or one in a box. The
    walk enters it but does not count it as an item, and it also ticks a row
    with a box and presses Enter. Any other row is a choice by its whole text,
    less the count on COUNTED, and no two rows on a screen may be one choice.
    On a screen with no highlight, a text box or a question, it presses Enter
    and, separately, y, the first time it lands there for the operators its
    path picked. When the menu leaves, the walk runs what it left to run. A
    screen whose title differs on every visit would keep the walk going for
    ever, so it fails after WALK_LIMIT paths in one state.

    It also keeps the menu.py lines that ran, each title, row and status drawn,
    each argv token a fake verb received, each str a screen function returned,
    and (state, path, command) for each path that ran a command. In a command,
    NAME stands for each operator the path picked and for the name Start an
    operator offers. Any other operator a command names keeps its name.
    """
    items, ran = set(), set()
    shown, passed, returned, commands = set(), set(), set(), set()

    def lines(frame, event, arg):
        if event == "line":
            ran.add(frame.f_lineno)
        elif event == "return" and isinstance(arg, str):
            returned.add(arg)
        return lines

    def calls(frame, _event, _arg):
        code = frame.f_code
        if code.co_filename == menu.__file__ and code.co_name not in PRIMITIVES:
            return lines
        return None

    previous = sys.gettrace()
    sys.settrace(calls)
    try:
        for kind, make in states:
            given = make()
            names = {op.name for op in (*given.running, *given.offline)}
            names |= {*given.recoverable, given.default_name()}
            todo, seen, boxes, taken = deque([((), ())]), {}, set(), 0
            while todo:
                taken += 1
                assert taken <= WALK_LIMIT, (
                    f"The walk took {WALK_LIMIT} paths in the {kind} state and had "
                    f"more to take. A screen whose title changes on every visit, "
                    f"such as one that counts, is new each time the walk lands "
                    f"on it. Raise WALK_LIMIT only if the menu grew.")
                path, named = todo.popleft()
                robot = Robot(path, then=["down"] * 12)
                actions = make()
                left = menu.run(robot.keys(), robot.render, actions)
                if isinstance(left, menu.Leave):
                    with redirect_stdout(io.StringIO()):
                        left.call()
                shown.update(text for frame in robot.frames
                             for text in (frame.title, *frame.rows, frame.status))
                received = {"start": actions.started, "stop": actions.stopped,
                            "rename": actions.renamed, "delete": actions.deleted,
                            "recover": actions.recovered, "attach": actions.attached}
                passed.update(token for calls in received.values() for argv in calls
                              for token in argv)
                picked = {*(label for label in _labels(path) if label in names),
                          given.default_name()}
                command = tuple((verb, *(NAME if token in picked else token
                                         for token in argv))
                                for verb, calls in received.items() for argv in calls)
                if command:
                    commands.add((kind, named, command))
                landed = robot.landed
                if landed is None:
                    continue
                section = {op.name: RUNNING for op in actions.running}
                section.update({op.name: OFFLINE for op in actions.offline})
                if landed.highlight is None:
                    box = (landed.title, *sorted(picked))
                    if box not in boxes:
                        boxes.add(box)
                        todo += [(path + (ENTER,), named + (ENTER,)),
                                 (path + (Key("y"),), named + (Key("y"),))]
                    continue
                known = seen.setdefault(landed.title, set())
                survey = robot.frames[robot.landed_at:robot.landed_at + 13]
                labels = list({frame.highlight: frame.rows[frame.highlight]
                               for frame in survey if frame.title == landed.title
                               and frame.highlight is not None}.values())
                choices = [_choice(label) for label in labels if not DATA_ROW.match(label)]
                assert len(choices) == len(set(choices)), (
                    f"Two rows on {landed.title!r} offer one choice: {choices}")
                found = set()
                for label in labels:
                    data = DATA_ROW.match(label)
                    boxed = label.startswith(("[ ] ", "[x] "))
                    if data:
                        step = data.group(1)
                        as_named = NAME if boxed else section[step]
                    else:
                        step = as_named = _choice(label)
                    if as_named in known:
                        continue
                    found.add(as_named)
                    if not data:
                        items.add((kind, named + (step,)))
                    todo.append((path + (step,), named + (as_named,)))
                    if boxed:
                        todo.append((path + (Toggle(step), ENTER),
                                     named + (Toggle(NAME), ENTER)))
                known |= found
    finally:
        sys.settrace(previous)
    return Walk(frozenset(items), frozenset(ran), frozenset(shown),
                frozenset(passed), frozenset(returned), frozenset(commands))


def _named(case: Case) -> tuple:
    """The case's menu path, naming each operator the way the walk does."""
    kinds = {name: OFFLINE if state == "offline" else RUNNING
             for name, (_, state) in case.given.items()}
    return tuple(Toggle(NAME) if isinstance(step, Toggle) else kinds.get(step, step)
                 for step in case.menu)


def _outside_primitives() -> list:
    """The top-level statements of menu.py, less the primitives' definitions."""
    tree = ast.parse(Path(menu.__file__).read_text(encoding="utf-8"))
    return [node for node in tree.body
            if not (isinstance(node, ast.FunctionDef) and node.name in PRIMITIVES)]


def _screens() -> list:
    """(name, statements) for each function in menu.py outside the primitives.

    A docstring is not one of the statements.
    """
    return [(node.name, node.body[1:] if ast.get_docstring(node) is not None
             else node.body)
            for node in _outside_primitives() if isinstance(node, ast.FunctionDef)]


def _screen_statements() -> dict:
    """Line to function, for each statement in menu.py outside the primitives."""
    return {inner.lineno: name for name, body in _screens() for statement in body
            for inner in ast.walk(statement) if isinstance(inner, ast.stmt)}


def _menu_strings() -> dict:
    """A pattern for each str literal in menu.py outside the primitives, to its line.

    A str that is a statement by itself, such as a docstring, is not one, and
    neither is a blank one. An f-string is one pattern whose fields match any
    text.
    """
    found, todo = {}, _outside_primitives()
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.JoinedStr):
            found["".join(re.escape(part.value) if isinstance(part, ast.Constant)
                          else "(?s:.*)" for part in node.values)] = node.lineno
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip():
                found[re.escape(node.value)] = node.lineno
        else:
            todo += ast.iter_child_nodes(node)
    return found


def _two_sided(case: Case) -> bool:
    return bool(case.argv) and case.menu is not None


def test_every_verb_has_a_case():
    assert "help" in _front_words()
    assert _front_words() - _words(CASES) == set()


def test_every_option_has_a_case():
    for name in NOT_TYPED:
        assert (CLI / name).is_file(), name
    options = _typed_options()
    assert {("start", "--name"), ("start", "--name="), ("start", "--attach"),
            ("start", "--fresh"), ("start", "--agent="), ("delete", "--yes"),
            ("handoff", "--status"), ("recover", "--all"), ("recover", "--help"),
            (None, "--home"), (None, "--home="), (None, "--help")} <= options
    missing = options - _typed_by_cases(CASES)
    assert sorted((verb or "", spelling) for verb, spelling in missing) == []


def test_no_option_comes_from_another_package():
    """The scan above reads the literals in operator_cli, so an option that
    operator_cli imports from the kernel would escape it. None may sit in what
    operator_cli imports, however deep. _options_in says where it looks."""
    found, resolved = [], set()
    for path in sorted(CLI.glob("*.py")):
        if path.name in NOT_TYPED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                bound.update((alias.asname or alias.name,
                              _from_import(node.module, alias.name))
                             for alias in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name if alias.asname else alias.name.partition(".")[0]
                    bound[alias.asname or name] = _module(name)
        reads = {ast.unparse(node): _read(node, bound) for node in ast.walk(tree)
                 if isinstance(node, (ast.Attribute, ast.Call))}
        resolved.update(name for name, value in (*bound.items(), *reads.items())
                        if value is not None)
        found += [(path.name, name, option)
                  for name, value in (*bound.items(), *reads.items())
                  for option in _options_in(value)]
    assert {"at_dashdash", "MUX", "operators", "operators.find",
            "getattr(operator_kernel, '__file__', None)"} <= resolved
    assert sorted(found) == []


def test_the_import_check_looks_wherever_an_option_can_sit():
    """The test above passes only when it finds nothing. These show what it
    would find: an option deep in a collection, on a class or its base, on an
    object, or on a function, and an attribute that getattr reads by a literal
    name. It does not search a module, or read a name built when code runs."""
    class Flags:
        fresh = "--fresh"

    class Kept(Flags):
        pass

    def parse():
        pass

    parse.option = "--remote"
    here = sys.modules[__name__]
    assert _options_in({"start": ("--name", ["--attach="])}) == ["--attach=", "--name"]
    assert _options_in(Kept) == _options_in(Kept()) == ["--fresh"]
    assert _options_in(SimpleNamespace(parse=parse)) == ["--remote"]
    assert _options_in(here) == []
    assert "--fresh" in _options_in(CASES)
    reads = [_read(ast.parse(text, mode="eval").body, {"here": here})
             for text in ("here.STAYS", "getattr(here, 'STAYS')",
                          "getattr(here, 'STAY' + 'S')")]
    assert reads == [STAYS, STAYS, None]


def test_no_parser_takes_an_abbreviation():
    """argparse reads --al as --all unless told not to, and no case types --al."""
    parsers = [(path.name, any(word.arg == "allow_abbrev"
                               and getattr(word.value, "value", True) is False
                               for word in call.keywords))
               for path in sorted(CLI.glob("*.py")) if path.name not in NOT_TYPED
               for call in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
               if _is_parser(call)]
    assert parsers and all(refuses for _, refuses in parsers), parsers


def test_every_menu_item_has_a_case():
    """Each choice the walk found, on the path it took there, begins a case's
    menu path. Attach for a running operator does not cover Attach for a
    stopped one. With one session to recover, the main menu says none need
    recovery only after Recover ran, so the walk finds that row when it lands
    there a second time, and the path that ran Recover is the path to it."""
    walk = _walk()
    items = {path for _, path in walk.items}
    assert {("Start an operator",), ("Quit",), ("Recover operator sessions",),
            ("List operators", RUNNING, "Attach"),
            ("List operators", OFFLINE, "Delete")} <= items
    assert ("busy", ("Recover operator sessions", Toggle(NAME), ENTER,
                     "No operators need recovery.")) in walk.items
    paths = {_named(case) for case in CASES if case.menu is not None}
    assert sorted(item for item in items
                  if not any(path[:len(item)] == item for path in paths)) == []


def test_a_menu_path_runs_the_same_command_in_every_state():
    """Where the walk took a path in several states, or for several operators
    in one, every run that ran a command ran the same one, once each operator
    the path picked is NAME. So a choice that acts on an operator other than
    the one picked fails. A path may run nothing in one state, as Start an
    operator does when the name is refused."""
    commands, states = {}, {}
    for state, path, command in _walk().commands:
        commands.setdefault(path, set()).add(command)
        states.setdefault(path, set()).add(state)
    assert states[("List operators", OFFLINE, "Start")] == {"busy", "many"}
    assert states[("Start an operator", ENTER)] == {"idle", "busy", "many"}
    assert {path: ran for path, ran in commands.items() if len(ran) > 1} == {}


def test_the_walk_sees_a_verb_act_on_another_operator():
    """A positive control for the test above. When Stop and Rename act on the
    first operator of their section, whichever one the menu gave them, the
    walk finds two commands on each of their paths."""
    commands = {}
    for _, path, command in _walk((("careless", _careless),)).commands:
        commands.setdefault(path, set()).add(command)
    assert commands[("List operators", RUNNING, "Stop")] == {
        (("stop", NAME),), (("stop", "alpha"),)}
    assert commands[("List operators", OFFLINE, "Rename", ENTER)] == {
        (("rename", NAME, NAME),), (("rename", "bravo", NAME),)}


def test_the_screens_branch_only_in_statements():
    """The walk sees which lines ran, so a line that can go two ways hides the
    way it never went. Outside the primitives, menu.py has no conditional
    expression, no and or or, no comprehension that filters, and no line that
    starts two statements."""
    nodes = [inner for node in _outside_primitives() for inner in ast.walk(node)]
    inline = sorted(node.lineno for node in nodes
                    if isinstance(node, (ast.IfExp, ast.BoolOp))
                    or isinstance(node, COMPREHENSIONS)
                    and any(loop.ifs for loop in node.generators))
    starts = [node.lineno for node in nodes if isinstance(node, ast.stmt)]
    assert inline == []
    assert sorted({line for line in starts if starts.count(line) > 1}) == []


def test_the_walk_runs_every_statement_of_the_screens():
    """A branch the walk never takes could hold a choice that has no case."""
    statements = _screen_statements()
    assert {"start_screen", "action_screen", "recover_screen"} <= set(statements.values())
    missing = {line: fn for line, fn in statements.items() if line not in _walk().ran}
    assert missing == {}


def test_every_string_in_the_menu_is_drawn_passed_or_returned_whole():
    """A choice or an option the walk never reached still has its string.

    Each str literal in menu.py outside the primitives, module constants
    included, must be the whole of a title, row or status the walk saw drawn,
    an argv token a fake verb received, or a str a screen function returned.
    So a piece of a text fails, such as a separator to join with. Build that
    text with an f-string. A label computed from data escapes.
    """
    walk, strings = _walk(), _menu_strings()
    assert {re.escape(text) for text in ("Delete? [y/N]", "--attach", "list")} <= set(strings)
    assert any("(?s:.*)" in pattern for pattern in strings)
    texts = walk.shown | walk.passed | walk.returned
    unused = {pattern: line for pattern, line in strings.items()
              if not any(re.fullmatch(pattern, text) for text in texts)}
    assert unused == {}


def test_the_menu_calls_the_typed_handlers():
    """Each verb a menu path reaches is bound to the typed command's handler.

    List operators is the exception. It draws its own screen from the split
    `operator list` prints.
    """
    reached = {_verb(argv) for case in CASES if _two_sided(case) for argv in case.argv}
    assert {"start", "recover", "list"} <= reached
    for verb in reached - {"list"}:
        assert getattr(cli._Actions, verb) is cli.HANDLERS[verb], verb


# ── the README says the same ────────────────────────────────────


def _readme(heading: str) -> str:
    """The text under ``heading`` as GitHub shows it, up to the next heading of
    any level. GitHub hides an HTML comment, and an unclosed one hides the rest."""
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", README.read_text(encoding="utf-8"),
                  flags=re.S)
    assert f"\n{heading}\n" in text, f"README.md has no {heading!r} heading"
    return re.split(r"\n#+ ", text.split(f"\n{heading}\n", 1)[1], maxsplit=1)[0]


def _body(text: str) -> list:
    """The body rows of the one table in ``text``: each line after its separator
    up to a blank line, which GitHub draws as a row with or without a leading |."""
    lines = text.splitlines()
    separators = [i for i, line in enumerate(lines)
                  if re.fullmatch(r"\|?( *:?-+:? *\|)+ *:?-*:? *", line)]
    assert len(separators) == 1, separators
    return list(takewhile(str.strip, lines[separators[0] + 1:]))


def _rows(text: str) -> list:
    """The stripped cells of each body row of the table in ``text``."""
    return [[cell.strip() for cell in row.strip().strip("|").split("|")]
            for row in _body(text)]


def _first_cells(text: str) -> list:
    return [cells[0] for cells in _rows(text)]


def _map_row(case: Case) -> str:
    """The README row for a case: its menu path, and its shortest argv.

    A given operator is NAME. A typed name is NAME, or NEW after a NAME. A
    ticked operator is each NAME the command line lists.
    """
    menu_cells, words = [], {}
    for step in case.menu:
        if isinstance(step, Text):
            words[step.value] = "NEW" if words else "NAME"
            menu_cells += [f"type {words[step.value]}", "Enter"]
        elif isinstance(step, Toggle):
            words[step.label] = "NAME ..."
            menu_cells.append("Space on each NAME")
        elif isinstance(step, Key):
            menu_cells.append("Enter" if step == ENTER else step.name)
        elif step in case.given:
            words[step] = "NAME"
            menu_cells.append("NAME")
        else:
            menu_cells.append(step)
    argv = min(case.argv, key=lambda argv: len(" ".join(argv)))
    command = " ".join(words.get(word, word) for word in argv)
    return f"| {' > '.join(menu_cells)} | `operator {command}` |"


def test_the_readme_maps_each_menu_choice_to_its_command():
    agree = [case for case in CASES if _two_sided(case) and not case.menu_expect]
    section = _readme("## Menu and command line")
    assert sorted(_body(section)) == sorted(
        {_map_row(case) for case in agree if case.code == 0})
    items = _walk().labels
    cells = _first_cells(section)
    mapped = {part for cell in cells for part in cell.split(" > ") if part in items}
    assert mapped == items & {label for case in agree for label in _labels(case.menu)}


def test_the_readme_names_what_only_the_command_line_can_do():
    both = [case for case in CASES if _two_sided(case)]
    words = _front_words() - _words(both)
    options = ({spelling.rstrip("=") for _, spelling in _typed_options()}
               - {spelling.rstrip("=") for _, spelling in _typed_by_cases(both)})
    cells = _first_cells(_readme("### Only on the command line"))
    shown = {token for cell in cells for token in re.findall(r"`([^`]+)`", cell)}
    assert shown == {f"operator {word}" for word in words} | options


def test_the_readme_names_what_only_the_menu_can_do():
    both = {label for case in CASES if _two_sided(case) for label in _labels(case.menu)}
    cells = _first_cells(_readme("### Only in the menu"))
    assert set(cells) == _walk().labels - both


@pytest.mark.parametrize("heading", ["### Only on the command line",
                                     "### Only in the menu"])
def test_each_thing_one_way_has_gives_a_reason(heading):
    rows = _rows(_readme(heading))
    assert rows
    shown = [re.sub(r"<[^>]*>|&#?\w+;", "", cells[-1]) for cells in rows]
    assert [cells for cells, reason in zip(rows, shown)
            if len(cells) != 2 or not re.search(r"\w", reason)] == []


def test_the_readme_explains_every_difference():
    """Each line of the section is a bullet. Each bullet is a case's doc or STAYS,
    word for word, and each doc is a bullet."""
    for case in CASES:
        if case.menu_expect:
            assert _two_sided(case) and case.doc, case.id
    section = _readme("### Where they behave differently")
    lines = [line for line in section.splitlines() if line.strip()]
    assert [line for line in lines if not line.startswith("- ")] == []
    bullets = {line[2:] for line in lines}
    assert bullets == {case.doc for case in CASES if case.menu_expect} | {STAYS}
