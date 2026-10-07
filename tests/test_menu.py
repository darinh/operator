"""Keyboard screens, driven by a scripted key list and fakes."""
from __future__ import annotations

from operator_cli.menu import Leave, Op, Row, action_screen, ask_text, confirm
from operator_cli.menu import list_screen, multi_select, render, run, select
from operator_cli.menu import start_screen


class Board:
    def __init__(self):
        self.frames = []

    def __call__(self, title, rows, highlight=None, status=""):
        self.frames.append({
            "title": title,
            "rows": list(rows),
            "highlight": highlight,
            "status": status,
        })


NAME_TITLE = ("Start an operator in C:\\work\\demo\n"
              "Enter starts it and attaches this terminal. Esc goes back.\n"
              "Name:")


class Actions:
    def __init__(self):
        self.cwd_value = r"C:\work\demo"
        self.name = "demo"
        self.running = []
        self.offline = []
        self.recoverable = []
        self.started = []
        self.stopped = []
        self.renamed = []
        self.deleted = []
        self.recovered = []
        self.attached = []
        self.problems = {}

    def start_problem(self, name):
        return self.problems.get(name)

    def recoverable_count(self):
        return len(self.recoverable)

    def recoverable_names(self):
        return list(self.recoverable)

    def cwd(self):
        return self.cwd_value

    def default_name(self):
        return self.name

    def sections(self):
        return list(self.running), list(self.offline)

    def start(self, argv):
        self.started.append(list(argv))
        name = argv[argv.index("--name") + 1]
        print(f"started {name} (pid 9)")
        if "--attach" not in argv:
            self.offline = [op for op in self.offline if op.name != name]
            self.running = [op for op in self.running if op.name != name]
            self.running.append(Op(name, self.cwd_value,
                                   f"{name}  ({self.cwd_value})", True))
        return 0

    def stop(self, argv):
        self.stopped.append(list(argv))
        print(f"stop requested for {argv[0]}")
        moved = [op for op in self.running if op.name == argv[0]]
        self.running = [op for op in self.running if op.name != argv[0]]
        for op in moved:
            self.offline.append(Op(op.name, op.cwd, f"{op.name}  ({op.cwd})", False))
        return 0

    def rename(self, argv):
        self.renamed.append(list(argv))
        print(f"renamed {argv[0]} to {argv[1]}")
        self.offline = [Op(argv[1], op.cwd, f"{argv[1]}  ({op.cwd})", False)
                        if op.name == argv[0] else op
                        for op in self.offline]
        return 0

    def delete(self, argv):
        self.deleted.append(list(argv))
        print(f"deleted {argv[0]}")
        self.offline = [op for op in self.offline if op.name != argv[0]]
        return 0

    def recover(self, names):
        self.recovered.append(list(names))
        self.recoverable = [name for name in self.recoverable if name not in names]
        print("recovered " + " ".join(names))
        return 0

    def attach(self, argv):
        self.attached.append(list(argv))
        print("attached-for-real")
        return 7


def test_render_clears_and_marks_the_highlighted_row(capsys):
    render("operator", ["Start an operator", "Quit"], highlight=0,
           status="started demo (pid 9)")
    assert capsys.readouterr().out == (
        "\x1b[2J\x1b[H"
        "started demo (pid 9)\n"
        "\n"
        "operator\n"
        "> Start an operator\n"
        "  Quit\n"
    )


def test_select_skips_headings_and_esc_returns_none():
    board = Board()
    rows = [Row("Running:", False), Row("1. alpha"),
            Row("Offline:", False), Row("(none)", False)]
    assert select("Operators", rows, iter(["down", "esc"]), board) is None
    assert board.frames[0]["highlight"] == 1
    assert board.frames[1]["highlight"] == 1


def test_confirm_takes_only_y_as_yes():
    for key, said in (("y", True), ("Y", True), ("n", False), ("enter", False),
                      ("esc", False), ("q", False)):
        assert confirm(["Delete? [y/N]"], iter([key]), Board()) is said, key


def test_ask_text_edits_a_prefill_and_esc_cancels():
    board = Board()
    assert ask_text("Operator name:", iter(["backspace", "x", "enter"]),
                    board, prefill="ab") == "ax"
    assert board.frames[0]["rows"] == ["ab"]
    assert ask_text("Operator name:", iter(["esc"]), board, prefill="ab") is None


def test_enter_on_start_leaves_to_start_the_default_name_and_attach():
    actions = Actions()
    board = Board()
    done = run(iter(["enter", "enter"]), board, actions)
    assert isinstance(done, Leave)
    assert actions.started == []
    assert board.frames[1]["title"] == NAME_TITLE
    assert board.frames[1]["rows"] == ["demo"]
    assert done.call() == 0
    assert actions.started == [["--name", "demo", "--attach"]]


def test_esc_on_the_name_starts_nothing():
    actions = Actions()
    board = Board()
    assert start_screen(iter(["esc"]), board, actions) == ""
    assert actions.started == []


def test_a_refused_name_is_shown_and_asked_again():
    actions = Actions()
    actions.name = "app"
    actions.problems = {"app": r"app already works in C:\old\app. Choose another name."}
    board = Board()
    keys = ["enter", "backspace", "backspace", "backspace", "n", "e", "w", "enter"]
    done = start_screen(iter(keys), board, actions)
    assert isinstance(done, Leave)
    asked = [frame for frame in board.frames if frame["title"] == NAME_TITLE]
    assert asked[0]["status"] == ""
    assert asked[1]["rows"] == ["app"]
    assert asked[1]["status"] == (
        r"app already works in C:\old\app. Choose another name.")
    assert done.call() == 0
    assert actions.started == [["--name", "new", "--attach"]]


def test_an_empty_name_is_asked_again_and_never_started():
    actions = Actions()
    actions.name = ""
    actions.problems = {"": "a name is needed"}
    board = Board()
    assert start_screen(iter(["space", "enter", "esc"]), board, actions) == ""
    assert actions.started == []
    assert board.frames[-1]["status"] == "a name is needed"
    assert board.frames[-1]["rows"] == [" "]


def test_list_numbers_each_section_and_skips_headings():
    actions = Actions()
    actions.running = [Op("alpha", r"C:\a", r"alpha  (C:\a)  pid 4", True)]
    actions.offline = [Op("bravo", r"C:\b", r"bravo  (C:\b)", False)]
    board = Board()
    assert run(iter(["down", "enter", "down", "esc", "esc"]), board, actions) == 0
    listed = [frame for frame in board.frames if frame["title"] == "Operators"]
    assert listed[0]["rows"] == [
        "Running:",
        r"1. alpha  (C:\a)  pid 4",
        "Offline:",
        r"1. bravo  (C:\b)",
    ]
    assert listed[0]["highlight"] == 1
    assert listed[1]["highlight"] == 3
    assert listed[1]["rows"][3] == r"1. bravo  (C:\b)"


def test_an_empty_section_shows_none_and_cannot_be_selected():
    actions = Actions()
    actions.offline = [Op("bravo", r"C:\b", r"bravo  (C:\b)", False)]
    board = Board()
    assert list_screen(iter(["esc"]), board, actions) == ""
    assert board.frames[0]["rows"] == [
        "Running:",
        "(none)",
        "Offline:",
        r"1. bravo  (C:\b)",
    ]
    assert board.frames[0]["highlight"] == 3


def test_attach_leaves_the_tool_and_stop_returns_to_the_list(capsys):
    actions = Actions()
    actions.running = [Op("alpha", r"C:\a", r"alpha  (C:\a)  pid 4", True)]
    board = Board()
    outcome = run(iter(["down", "enter", "enter", "enter"]), board, actions)
    assert isinstance(outcome, Leave)
    assert "attached-for-real" not in capsys.readouterr().out
    assert outcome.call() == 7
    assert capsys.readouterr().out == "attached-for-real\n"

    board = Board()
    assert run(iter(["down", "enter", "enter", "down", "enter", "esc", "esc"]),
               board, actions) == 0
    assert actions.stopped == [["alpha"]]
    listed = [frame for frame in board.frames if frame["title"] == "Operators"]
    assert listed[-1]["status"] == "stop requested for alpha"
    assert r"1. alpha  (C:\a)" in listed[-1]["rows"]
    assert listed[-1]["rows"][1] == "(none)"


def test_offline_start_returns_to_the_list_under_running():
    actions = Actions()
    actions.offline = [Op("alpha", r"C:\work\demo", r"alpha  (C:\work\demo)", False)]
    board = Board()
    assert run(iter(["down", "enter", "enter", "enter", "esc", "esc"]),
               board, actions) == 0
    assert actions.started == [["--name", "alpha"]]
    listed = [frame for frame in board.frames if frame["title"] == "Operators"]
    assert listed[-1]["status"] == "started alpha (pid 9)"
    assert listed[-1]["rows"] == [
        "Running:",
        r"1. alpha  (C:\work\demo)",
        "Offline:",
        "(none)",
    ]


def test_start_and_attach_leaves_without_capturing(capsys):
    actions = Actions()
    actions.offline = [Op("alpha", r"C:\a", r"alpha  (C:\a)", False)]
    board = Board()
    outcome = run(iter(["down", "enter", "enter", "down", "enter"]), board, actions)
    assert isinstance(outcome, Leave)
    assert actions.started == []
    assert outcome.call() == 0
    assert actions.started == [["--name", "alpha", "--attach"]]
    assert "started alpha (pid 9)" in capsys.readouterr().out
    assert all(frame["status"] != "started alpha (pid 9)" for frame in board.frames)


def test_rename_starts_from_the_current_name_and_the_list_shows_the_new_one():
    actions = Actions()
    actions.offline = [Op("alpha", r"C:\a", r"alpha  (C:\a)", False)]
    board = Board()
    keys = ["down", "enter", "enter", "down", "down", "enter",
            "backspace", "backspace", "backspace", "backspace", "backspace",
            "b", "r", "a", "v", "o", "enter", "esc", "esc"]
    assert run(iter(keys), board, actions) == 0
    assert actions.renamed == [["alpha", "bravo"]]
    named = [frame for frame in board.frames if frame["title"] == "Operator name:"]
    assert named[0]["rows"] == ["alpha"]
    listed = [frame for frame in board.frames if frame["title"] == "Operators"]
    assert r"1. bravo  (C:\a)" in listed[-1]["rows"]
    assert listed[-1]["status"] == "renamed alpha to bravo"


def test_delete_confirm_shows_the_repo_and_only_y_deletes():
    actions = Actions()
    actions.offline = [Op("alpha", r"C:\a", r"alpha  (C:\a)", False)]
    board = Board()
    keys = ["down", "enter", "enter", "down", "down", "down", "enter", "n",
            "esc", "esc", "esc"]
    assert run(iter(keys), board, actions) == 0
    assert actions.deleted == []
    declined = next(i for i, frame in enumerate(board.frames)
                    if frame["title"].startswith("Deletes operator"))
    assert board.frames[declined + 1]["title"] == "alpha"
    assert board.frames[declined + 1]["rows"] == [
        "Start", "Start and attach", "Rename", "Delete"]
    confirm_frame = next(frame for frame in board.frames
                         if frame["title"].startswith("Deletes operator"))
    assert confirm_frame["title"] == "Deletes operator alpha and all of its settings."
    assert confirm_frame["rows"] == [r"Repo: C:\a", "Delete? [y/N]"]
    assert any(r"1. alpha  (C:\a)" in frame["rows"]
               for frame in board.frames if frame["title"] == "Operators")

    actions = Actions()
    actions.offline = [Op("alpha", r"C:\a", r"alpha  (C:\a)", False)]
    board = Board()
    keys = ["down", "enter", "enter", "down", "down", "down", "enter", "y",
            "esc", "esc"]
    assert run(iter(keys), board, actions) == 0
    assert actions.deleted == [["alpha", "--yes"]]
    listed = [frame for frame in board.frames if frame["title"] == "Operators"]
    assert listed[-1]["status"] == "deleted alpha"
    assert listed[-1]["rows"] == ["Running:", "(none)", "Offline:", "(none)"]


def test_recover_row_names_the_count_or_says_none_need_it():
    actions = Actions()
    actions.recoverable = ["alpha", "bravo"]
    board = Board()
    assert run(iter(["esc"]), board, actions) == 0
    assert board.frames[0]["rows"][2] == "Recover operator sessions (2)"

    actions.recoverable = []
    board = Board()
    assert run(iter(["down", "down", "enter", "esc"]), board, actions) == 0
    assert board.frames[0]["rows"][2] == "No operators need recovery."
    assert board.frames[-1]["title"] == "operator"
    assert actions.recovered == []


def test_space_toggles_and_enter_recovers_only_the_toggled_names():
    actions = Actions()
    actions.recoverable = ["alpha", "bravo", "charlie"]
    board = Board()
    keys = ["down", "down", "enter", "space", "down", "space", "up", "space",
            "enter", "esc"]
    assert run(iter(keys), board, actions) == 0
    assert actions.recovered == [["bravo"]]
    recover_frames = [frame for frame in board.frames
                      if frame["title"] == "Recover operator sessions"]
    assert recover_frames[1]["rows"][0] == "[x] 1. alpha"
    assert recover_frames[-1]["rows"] == [
        "[ ] 1. alpha",
        "[x] 2. bravo",
        "[ ] 3. charlie",
    ]
    assert board.frames[-1]["status"] == "recovered bravo"
    assert board.frames[-1]["title"] == "operator"


def test_enter_with_nothing_toggled_goes_back():
    actions = Actions()
    actions.recoverable = ["alpha"]
    board = Board()
    assert run(iter(["down", "down", "enter", "enter", "esc"]), board, actions) == 0
    assert actions.recovered == []
    assert [frame["title"] for frame in board.frames] == [
        "operator", "operator", "operator", "Recover operator sessions", "operator"]
    assert board.frames[-1]["status"] == ""


def test_esc_backs_out_of_every_screen():
    actions = Actions()
    actions.running = [Op("alpha", r"C:\a", r"alpha  (C:\a)", True)]
    actions.offline = [Op("bravo", r"C:\b", r"bravo  (C:\b)", False)]
    actions.recoverable = ["alpha"]
    def titles(keys, screen=run, *args):
        board = Board()
        assert screen(*args, iter(keys), board, actions) in (0, "")
        return [frame["title"] for frame in board.frames]

    assert titles(["esc"]) == ["operator"]
    assert titles(["esc"], start_screen) == [NAME_TITLE]
    assert actions.started == []
    assert titles(["enter", "esc", "esc"], list_screen) == [
        "Operators", "alpha", "Operators"]
    assert titles(["down", "down", "enter", "esc", "esc"], action_screen,
                  actions.offline[0]) == [
                      "bravo", "bravo", "bravo", "Operator name:", "bravo"]
    assert actions.renamed == []
    assert titles(["down", "down", "enter", "esc", "esc"]) == [
        "operator", "operator", "operator", "Recover operator sessions", "operator"]
    assert actions.recovered == []
    assert multi_select("Recover operator sessions", ["alpha"],
                        iter(["esc"]), Board()) is None


def test_esc_on_the_main_menu_quits_0():
    for keys in (["esc"], ["down", "down", "down", "enter"]):
        board = Board()
        assert run(iter(keys), board, Actions()) == 0
        assert {frame["title"] for frame in board.frames} == {"operator"}
        assert board.frames[-1]["highlight"] == (0 if keys == ["esc"] else 3)
