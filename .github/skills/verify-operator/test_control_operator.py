"""Tests for the verify-operator harness and its fake copilot.

A harness bug does not produce a failed verification, it produces a *false* one:
a run that writes to the wrong home, launches the real Copilot, or tears down
the evidence is worse than no verification at all. So what the harness decides
before it hands off to a subprocess is tested here, separately from the live
pass. These tests spawn no operator, multiplexer or venv.

Not collected by the repository suite: `pyproject.toml` sets
``testpaths = ["tests"]``, and this file is deliberately outside it so the
skill stays self-contained and the kernel's budgets are unaffected. CI runs it
explicitly, with the fake copilot's tests beside it::

    python -m pytest .github/skills/verify-operator -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SKILL = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("verify_operator_control",
                                               _SKILL / "control_operator.py")
control = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = control
_spec.loader.exec_module(control)
fake = control._load_fake()


@pytest.fixture
def run(tmp_path) -> Path:
    """A run directory as `up` leaves it, without the venv's contents."""
    directory = tmp_path / "run"
    (directory / "home" / "projects" / "guid-1").mkdir(parents=True)
    for sub in ("scripts", "agents", "screens"):
        (directory / "artifacts" / sub).mkdir(parents=True)
    for sub in ("venv", "fake", "repo", "logs"):
        (directory / sub).mkdir()
    (directory / "run.json").write_text(json.dumps({
        "created": "2026-01-01T00:00:00Z", "checkout": str(tmp_path / "checkout"),
        "repo": str(directory / "repo"), "guid": "guid-1",
        "home": str(directory / "home"), "artifacts": str(directory / "artifacts"),
        "menu_session": "vo-test",
    }), encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    """`down` lists processes and the multiplexer. Nothing here may touch either."""
    monkeypatch.setattr(control, "_processes", lambda: [])
    monkeypatch.setattr(control, "_mux", lambda: None)


def _record_operator(run: Path, op_id: str, name: str, parent: str = "human") -> None:
    records = run / "home" / "operators"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{op_id}.json").write_text(json.dumps(
        {"id": op_id, "name": name, "cwd": str(run / "repo"), "parent": parent}),
        encoding="utf-8")


# ── the environment every child gets ─────────────────────────────────────


def test_the_child_is_told_to_use_the_runs_home(run):
    """The kernel captures `config.OPERATOR_HOME` at import, so only a variable
    set before the child starts redirects it."""
    assert control._env(run)["COPILOT_OPERATOR_HOME"] == str(run / "home")


def test_an_ambient_operator_home_does_not_win(run, monkeypatch):
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(Path.home() / ".operator"))
    assert control._env(run)["COPILOT_OPERATOR_HOME"] == str(run / "home")


def test_the_runs_venv_comes_first_on_path(run, monkeypatch):
    """The supervisor finds `copilot` with `shutil.which` in the environment
    `operator start` hands it. Anything ahead of the venv is a real Copilot."""
    monkeypatch.setenv("PATH", "C:\\elsewhere")
    env = control._env(run)
    first = env["PATH"].split(os.pathsep)[0]
    assert first == str(control._scripts_dir(run))
    assert env["PATH"].endswith("C:\\elsewhere")


def test_the_pane_is_told_how_to_find_the_venv_again(run):
    """psmux gives a pane the registry PATH. The fake reads this to undo that."""
    env = control._env(run)
    assert env["FAKE_PATH_FIRST"] == str(control._scripts_dir(run))
    assert env["FAKE_RUN"] == str(run)


def test_copilot_logs_land_in_the_run(run):
    """The fake writes its process log here, and the runner reads it from here."""
    assert control._env(run)["COPILOT_LOG_DIR"] == str(run / "logs")


def test_operator_never_learns_it_runs_inside_a_pane(run, monkeypatch):
    """Measured in the menu's pane: Start printed `sessions should be nested
    with care, unset PSMUX_SESSION to force` and `operator` exited 0."""
    for name in control.PANE_VARIABLES:
        monkeypatch.setenv(name, "vo-outer")
    env = control._env(run)
    assert not {k.upper() for k in env} & set(control.PANE_VARIABLES)


def test_the_child_environment_turns_the_warm_server_off(run):
    """A warm server holds its start directory open, and `down` then cannot
    remove the run. Measured: without the variable the directory stayed locked."""
    assert control._env(run)["PSMUX_NO_WARM"] == "1"


def test_every_multiplexer_call_turns_the_warm_server_off(monkeypatch):
    seen = {}

    def spy(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(control, "_mux", lambda: "psmux")
    monkeypatch.setattr(control.subprocess, "run", spy)
    control._mux_run(["ls"], env={"PATH": "x"})
    assert seen["env"]["PSMUX_NO_WARM"] == "1"
    assert seen["cwd"], "a psmux client must not start in the caller's directory"


# ── locating the checkout ────────────────────────────────────────────────


def test_the_checkout_under_test_is_the_one_this_file_is_in():
    found = control.checkout_root(_SKILL)
    assert (found / "pyproject.toml").is_file()
    assert _SKILL.is_relative_to(found)


def test_a_directory_outside_a_checkout_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control.checkout_root(tmp_path)


# ── what the harness drives ──────────────────────────────────────────────


def test_doctor_fails_a_run_whose_copilot_is_not_the_venvs(run, monkeypatch, capsys):
    """A run that resolves `operator` but not `copilot` launches a real one."""
    exe = control._venv_exe(run, "operator")
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    os.chmod(exe, 0o755)
    monkeypatch.setenv("PATH", "")
    assert control.main(["doctor", "--no-mux", "--run", str(run)]) == 1
    out = capsys.readouterr().out
    assert "PASS  operator resolves to the run's venv" in out
    assert "FAIL  copilot resolves to the run's venv" in out


def test_the_front_door_runs_the_venvs_operator_in_the_scratch_project(monkeypatch, run):
    seen = {}
    monkeypatch.setattr(control, "_invoke",
                        lambda run_, label, argv, cwd: seen.update(argv=argv, cwd=cwd))
    control.main(["operator", "--run", str(run), "--", "list"])
    assert seen["argv"] == [control._venv_exe(run, "operator"), "list"]
    assert seen["cwd"] == run / "repo"


def test_the_front_door_can_be_pointed_somewhere_else(monkeypatch, run, tmp_path):
    seen = {}
    monkeypatch.setattr(control, "_invoke",
                        lambda run_, label, argv, cwd: seen.update(cwd=cwd))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    control.main(["operator", "--run", str(run), "--cwd", str(elsewhere), "--", "list"])
    assert seen["cwd"] == elsewhere.resolve()


def test_flags_after_the_separator_reach_the_console_script(monkeypatch, run):
    seen = {}
    monkeypatch.setattr(control, "_invoke",
                        lambda run_, label, argv, cwd: seen.update(argv=argv))
    control.main(["operator", "--run", str(run), "--", "list", "--help"])
    assert seen["argv"][-2:] == ["list", "--help"]
    assert "--" not in seen["argv"]


def test_a_verb_sees_no_terminal_and_never_waits_on_the_callers_console(run, capsys):
    """`operator delete` asks a question when stdin is a terminal. Measured on
    Windows with DEVNULL: isatty() was True, and delete prompted and exited 1."""
    probe = [sys.executable, "-c", "import sys; print(sys.stdin.isatty(), sys.stdin.read() == '')"]
    assert control._invoke(run, "probe", probe, run) == 0
    assert capsys.readouterr().out.split() == ["False", "True"]


def test_the_menu_pane_runs_operator_through_the_runs_environment(monkeypatch, run):
    """A pane gets the registry PATH, so the menu inside it would start
    supervisors that find the real Copilot. `exec` sets PATH first."""
    calls = []
    monkeypatch.setattr(control, "_has_session", lambda s: bool(calls))
    monkeypatch.setattr(control, "_mux_run", lambda args, env=None, cwd=None:
                        calls.append((args, env)) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert control.main(["menu", "--run", str(run)]) == 0
    args, env = calls[0]
    pane = args[args.index("--") + 1:]
    assert pane[1:] == [str(Path(control.__file__).resolve()), "exec", "--hold",
                        "--run", str(run)]
    assert args[args.index("-s") + 1] == "vo-test"
    assert args[args.index("-c") + 1] == str(run / "repo")
    assert env["PATH"].split(os.pathsep)[0] == str(control._scripts_dir(run))


def test_exec_hold_stays_open_after_operator_exits_and_says_its_code(
        monkeypatch, run, capsys):
    """Without the hold, a menu that quits or attaches takes its session with it."""
    class Held(Exception):
        pass

    def hold(seconds):
        raise Held

    monkeypatch.setattr(control.subprocess, "call", lambda argv, env=None: 3)
    monkeypatch.setattr(control.time, "sleep", hold)
    with pytest.raises(Held):
        control.main(["exec", "--hold", "--run", str(run)])
    assert "[operator exited 3]" in capsys.readouterr().out
    assert "menu: operator exited 3" in (
        control._artifacts(run) / "transcript.md").read_text(encoding="utf-8")


def test_exec_without_hold_returns_operators_exit_code(monkeypatch, run):
    monkeypatch.setattr(control.subprocess, "call", lambda argv, env=None: 3)
    assert control.main(["exec", "--run", str(run)]) == 3


def test_menu_again_replaces_the_held_pane(monkeypatch, run):
    """Every attach leaves the old pane open. The next `menu` must not need a
    multiplexer command typed by hand, which a tmux host spells differently."""
    live, calls = {"vo-test"}, []

    def mux(args, env=None, cwd=None):
        calls.append(args[0])
        if args[0] == "kill-session":
            live.discard(args[-1])
        if args[0] == "new-session":
            live.add("vo-test")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(control, "_has_session", lambda s: s in live)
    monkeypatch.setattr(control, "_mux_run", mux)
    assert control.main(["menu", "--run", str(run)]) == 0
    assert calls == ["kill-session", "new-session"]


@pytest.mark.parametrize("listed, code, expected", [
    ("vo-test2\nop-1\n", 0, False),
    ("vo-test\nvo-test2\n", 0, True),
    ("", 1, False),
], ids=["sibling-only", "exact", "no-server"])
def test_a_session_is_found_by_its_exact_name(monkeypatch, listed, code, expected):
    """tmux takes `-t vo-test` to mean vo-test2 when no vo-test exists, so a
    has-session check would let menu or down kill a sibling run's session."""
    monkeypatch.setattr(control, "_mux_run", lambda args, env=None, cwd=None:
                        SimpleNamespace(returncode=code, stdout=listed, stderr=""))
    assert control._has_session("vo-test") is expected


def _targets_named(run, monkeypatch) -> list[tuple[str, str]]:
    """Drive every verb that names a session and return each `-t` it passed."""
    _record_operator(run, "op-11111111", "lead")
    live, targets = {"vo-test", "op-11111111"}, []

    def mux(args, env=None, cwd=None):
        if "-t" in args:
            targets.append((args[0], args[args.index("-t") + 1]))
        return SimpleNamespace(returncode=0, stdout="screen", stderr="")

    monkeypatch.setattr(control, "_mux", lambda: "mux")
    monkeypatch.setattr(control, "_has_session", lambda s: s in live)
    monkeypatch.setattr(control, "_mux_run", mux)
    control.main(["menu", "--run", str(run)])
    control.main(["keys", "--run", str(run), "--text", "hi", "Enter"])
    control.main(["screen", "--run", str(run), "--target", "lead", "--label", "x"])
    control.cmd_down(SimpleNamespace(run=str(run)))
    return targets


def test_on_tmux_every_target_is_exact(run, monkeypatch):
    """Measured on tmux 3.4 with only vo-pfx2 alive: a bare `-t vo-pfx` read
    vo-pfx2's pane, typed into it, and killed it."""
    monkeypatch.setattr(control, "PREFIX_TARGETS", True)
    assert _targets_named(run, monkeypatch) == [
        ("kill-session", "=vo-test"), ("send-keys", "=vo-test:"),
        ("send-keys", "=vo-test:"), ("capture-pane", "=op-11111111:"),
        ("kill-session", "=op-11111111"), ("kill-session", "=vo-test")]


def test_on_psmux_every_target_is_bare(run, monkeypatch):
    """psmux 3.3.7 matches a bare name exactly and will not kill `=NAME`."""
    monkeypatch.setattr(control, "PREFIX_TARGETS", False)
    assert _targets_named(run, monkeypatch) == [
        ("kill-session", "vo-test"), ("send-keys", "vo-test"), ("send-keys", "vo-test"),
        ("capture-pane", "op-11111111"), ("kill-session", "op-11111111"),
        ("kill-session", "vo-test")]


def test_keys_fails_when_its_text_does_not_arrive(run, monkeypatch, capsys):
    monkeypatch.setattr(control, "_mux_run", lambda args, env=None, cwd=None:
                        SimpleNamespace(returncode=1, stdout="", stderr="no session"))
    assert control.main(["keys", "--run", str(run), "--text", "hi"]) == 1
    assert "no session" in capsys.readouterr().err


def test_a_target_is_the_menu_or_an_operator_by_name_or_id(run):
    _record_operator(run, "op-11111111", "lead")
    assert control._session(run, "menu") == "vo-test"
    assert control._session(run, "lead") == "op-11111111"
    assert control._session(run, "op-11111111") == "op-11111111"
    with pytest.raises(SystemExit):
        control._session(run, "nobody")


def _wait(run: Path, **given) -> int:
    args = dict(run=str(run), file=None, screen=None, contains=None, timeout=0)
    return control.cmd_wait(SimpleNamespace(**{**args, **given}))


ESCAPES = ["", "absolute", "../outside.txt", "artifacts/../../outside.txt"]
if os.name == "nt":
    ESCAPES += ["C:outside.txt", "/outside.txt", "\\outside.txt"]


@pytest.mark.parametrize("pattern", ESCAPES)
def test_wait_refuses_a_file_pattern_that_leaves_the_run(run, pattern):
    """A green wait must mean a file the run made. glob follows `..`, and on
    Windows a drive or a leading slash makes glob raise instead."""
    (run.parent / "outside.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    pattern = str(run / "artifacts" / "x") if pattern == "absolute" else pattern
    with pytest.raises(SystemExit, match="--file"):
        _wait(run, file=pattern)


def test_wait_does_not_follow_a_link_out_of_the_run(run):
    """A pattern can stay inside the run while a junction or symlink it
    matches through points outside it."""
    outside = run.parent / "elsewhere"
    outside.mkdir()
    (outside / "proof.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    link = run / "artifacts" / "out"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        os.symlink(outside, link, target_is_directory=True)
    assert (link / "proof.txt").read_text(encoding="utf-8") == "SECRET-OUTSIDE"
    assert _wait(run, file="artifacts/out/proof.txt") == 1
    assert _wait(run, file="artifacts/**/proof.txt") == 1


def _named(run: Path, verb: str, text: str):
    calls = {
        "agent": lambda: control.cmd_agent(SimpleNamespace(
            run=str(run), name=text, session=None, steps=[])),
        "screen": lambda: control.cmd_screen(SimpleNamespace(
            run=str(run), target="menu", label=text)),
        "evidence": lambda: control.cmd_evidence(SimpleNamespace(run=str(run), label=text)),
        "up": lambda: control.cmd_up(SimpleNamespace(root=str(run.parent), run_id=text)),
    }
    return calls[verb]()


@pytest.mark.parametrize("verb", ["agent", "screen", "evidence", "up"])
@pytest.mark.parametrize("text", ESCAPES)
def test_a_name_that_would_leave_its_directory_is_refused(run, verb, text):
    """A script name, a label and a run id each become a path under the run,
    or under --root for a run id, so each is held to wait --file's rule."""
    if verb == "up" and text == "":
        pytest.skip("an empty --run-id asks up to make one")
    text = str(run.parent / "sib") if text == "absolute" else text
    with pytest.raises(SystemExit, match=f"^{verb}: "):
        _named(run, verb, text)
    assert sorted(p.name for p in run.parent.iterdir()) == ["run"]


def test_a_file_that_cannot_be_read_yet_is_not_found_yet(run, monkeypatch):
    """The supervisor deletes restart markers as it consumes them, and Windows
    refuses to read a file that is pending delete."""
    (run / "artifacts" / "gone.txt").write_text("x", encoding="utf-8")

    def vanished(self, *args, **kwargs):
        raise FileNotFoundError(self)

    monkeypatch.setattr(control.Path, "read_text", vanished)
    assert _wait(run, file="artifacts/gone.txt") == 1


def test_waiting_on_an_operator_outlasts_its_record_appearing(run, monkeypatch):
    """A fake's start step records the child after the wait began."""
    polls = []

    def capture(session):
        polls.append(session)
        return "steps done, listening"

    monkeypatch.setattr(control, "_capture", capture)
    monkeypatch.setattr(control.time, "sleep",
                        lambda seconds: _record_operator(run, "op-44444444", "scout"))
    assert _wait(run, screen="scout", contains="steps done", timeout=30) == 0
    assert polls == ["op-44444444"]


# ── agent scripts ────────────────────────────────────────────────────────


def test_agent_writes_the_steps_the_fake_will_find(run):
    code = control.main(["agent", "--run", str(run), "lead",
                         '{"op": ["start", "scout", "role=scout"]}', '{"sleep": 1}'])
    assert code == 0
    path = fake.script_for(run / "artifacts" / "scripts", "lead", 1)
    assert json.loads(path.read_text("utf-8")) == [
        {"op": ["start", "scout", "role=scout"]}, {"sleep": 1}]


def test_agent_writes_a_session_script_where_the_fake_looks_for_it(run):
    control.main(["agent", "--run", str(run), "lead", '{"sleep": 1}'])
    control.main(["agent", "--run", str(run), "--session", "2", "lead", '{"exit": 0}'])
    scripts = run / "artifacts" / "scripts"
    assert fake.script_for(scripts, "lead", 1).name == "lead.json"
    assert fake.script_for(scripts, "lead", 2).name == "lead.s2.json"


@pytest.mark.parametrize("step", [
    '{"run": ["list"]}',
    '{"on": "ping", "do": [{"bogus": 1}]}',
    '"just text"',
    "not json",
])
def test_agent_refuses_a_step_the_fake_cannot_run_and_writes_nothing(run, step):
    assert control.main(["agent", "--run", str(run), "lead", step]) == 2
    assert not list((run / "artifacts" / "scripts").iterdir())


# ── evidence ─────────────────────────────────────────────────────────────


def test_evidence_captures_the_state_the_skill_promises(run):
    home = run / "home"
    (home / "operator.log").write_text("log line\n", encoding="utf-8")
    (home / "projects" / "catalog.csv").write_text("repo,guid\n", encoding="utf-8")
    _record_operator(run, "op-22222222", "scout", parent="op-11111111")
    inbox = home / "mail" / "op-11111111" / "pending"
    inbox.mkdir(parents=True)
    (inbox / "1-a.json").write_text("{}", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))

    captured = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert {"operator.log", "projects__catalog.csv", "MANIFEST.txt",
            "operators__op-22222222.json",
            "mail__op-11111111__pending__1-a.json"} <= captured


def test_evidence_captures_both_halves_of_a_handoff(run):
    """A handoff file with no marker is a checkpoint. A marker with no file is a
    session that ended leaving nothing. Either alone is ambiguous."""
    handoff = run / "home" / "projects" / "guid-1" / "handoff"
    handoff.mkdir(parents=True)
    (handoff / "op-a.md").write_text("# Handoff\n", encoding="utf-8")
    (run / "home" / "restart").mkdir(parents=True, exist_ok=True)
    (run / "home" / "restart" / "op-a").touch()

    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))
    names = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert "projects__guid-1__handoff__op-a.md" in names
    assert "restart__op-a" in names


def test_two_labels_do_not_overwrite_each_other(run):
    (run / "home" / "operator.log").write_text("a\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="before"))
    (run / "home" / "operator.log").write_text("a\nb\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="after"))
    before = (run / "artifacts" / "before" / "operator.log").read_text("utf-8")
    after = (run / "artifacts" / "after" / "operator.log").read_text("utf-8")
    assert before != after


# ── teardown ─────────────────────────────────────────────────────────────


def test_teardown_removes_the_instance_and_keeps_the_proof(run):
    (run / "artifacts" / "transcript.md").write_text("proof\n", encoding="utf-8")
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0
    for name in ("home", "venv", "fake", "repo", "logs"):
        assert not (run / name).exists(), name
    assert (run / "artifacts" / "transcript.md").read_text("utf-8") == "proof\n"
    assert (run / "run.json").exists()
    assert "artifacts kept" in (run / "artifacts" / "down.txt").read_text("utf-8")


def test_teardown_snapshots_the_home_before_removing_it(run):
    _record_operator(run, "op-33333333", "lead")
    control.cmd_down(SimpleNamespace(run=str(run)))
    assert (run / "artifacts" / "at-down" / "operators__op-33333333.json").exists()


def test_teardown_twice_is_not_an_error(run):
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0


def test_teardown_removes_gits_read_only_objects(run):
    """Git marks its objects read-only, which plain rmtree cannot delete on Windows."""
    obj = run / "repo" / ".git" / "objects" / "ab" / "cdef"
    obj.parent.mkdir(parents=True)
    obj.write_text("x", encoding="utf-8")
    os.chmod(obj, 0o444)
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0
    assert not (run / "repo").exists()


def test_teardown_fails_when_anything_but_the_fake_was_launched(run):
    restart = run / "home" / "restart"
    restart.mkdir(parents=True)
    real = "C:\\Users\\x\\AppData\\Local\\Microsoft\\WindowsApps\\copilot.exe"
    (restart / "op-a.runner.log").write_text(
        f"2026-01-01 launching: {real} --yolo -i hello\n", encoding="utf-8")
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 1
    report = (run / "artifacts" / "down.txt").read_text("utf-8")
    assert "FAIL a copilot other than the fake was launched" in report
    assert real in report


def test_a_second_down_keeps_the_first_reports_failure(run):
    """down is idempotent, so it gets run twice. The first report is the proof."""
    restart = run / "home" / "restart"
    restart.mkdir(parents=True)
    (restart / "op-a.runner.log").write_text(
        "2026-01-01 launching: C:\\real\\copilot.exe -i hello\n", encoding="utf-8")
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 1
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0
    report = (run / "artifacts" / "down.txt").read_text("utf-8")
    assert "FAIL a copilot other than the fake was launched" in report
    assert report.index("down: FAILED") < report.index("down: clean")


def test_teardown_accepts_launches_of_the_fake(run):
    restart = run / "home" / "restart"
    restart.mkdir(parents=True)
    exe = str(control._venv_exe(run, "copilot"))
    (restart / "op-a.runner.log").write_text(
        f"2026-01-01 launching: {exe} --yolo -i hello\n", encoding="utf-8")
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_a_launch_spelled_in_another_case_is_still_the_fake(run):
    restart = run / "home" / "restart"
    restart.mkdir(parents=True)
    exe = str(control._venv_exe(run, "copilot")).upper()
    (restart / "op-a.runner.log").write_text(
        f"2026-01-01 launching: {exe} --yolo -i hello\n", encoding="utf-8")
    assert control._launches_outside(run) == []


def test_teardown_spares_the_caller_and_its_ancestors(run):
    """The shell that ran `down --run <run>` names the run too."""
    processes = [
        {"pid": 10, "ppid": 1, "text": f"pwsh -c python control_operator.py down --run {run}"},
        {"pid": 11, "ppid": 10, "text": f"python control_operator.py down --run {run}"},
        {"pid": 20, "ppid": 5, "text": f"python runner.py {run / 'home' / 'x.launch.json'}"},
        {"pid": 30, "ppid": 5, "text": "notepad.exe"},
    ]
    assert [p["pid"] for p in control._ours(processes, run, 11)] == [20]


def test_teardown_claims_only_its_own_run_directory(run):
    """Run ids share prefixes. This skill's own proof used demo and demo-menu2.
    A person reading the kept proof in an editor is not the run's either, but
    one editing the scratch repo holds what down is about to delete."""
    sibling = run.parent / (run.name + "-menu2")
    processes = [
        {"pid": 20, "ppid": 5, "text": f"{run / 'venv' / 'python.exe'} -m runner"},
        {"pid": 21, "ppid": 5, "text": f'python control_operator.py exec --run "{run}"'},
        {"pid": 22, "ppid": 5, "text": f"python control_operator.py exec --run {run}"},
        {"pid": 23, "ppid": 5, "text": f"code {run / 'repo' / 'notes.md'}"},
        {"pid": 30, "ppid": 5, "text": f"{sibling / 'venv' / 'python.exe'} -m runner"},
        {"pid": 31, "ppid": 5, "text": f"python x.py {run}2"},
        {"pid": 40, "ppid": 5, "text": f"code {run / 'artifacts' / 'transcript.md'}"},
    ]
    assert [p["pid"] for p in control._ours(processes, run, 99)] == [20, 21, 22, 23]


def test_down_refuses_a_directory_without_the_run_json_up_writes(tmp_path, monkeypatch):
    """`down --run .` from the checkout would otherwise kill whatever names it
    and remove its venv."""
    (tmp_path / "venv").mkdir()
    killed = []
    monkeypatch.setattr(control, "_processes",
                        lambda: [{"pid": 20, "ppid": 5, "text": f"python {tmp_path}"}])
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append(pid))
    with pytest.raises(SystemExit, match="no run at"):
        control.cmd_down(SimpleNamespace(run=str(tmp_path)))
    assert killed == [] and (tmp_path / "venv").is_dir()


def test_teardown_kills_what_outlived_stop_and_fails(run, monkeypatch):
    leftover = {"pid": 20, "ppid": 5, "text": f"python runner.py {run}"}
    killed = []
    monkeypatch.setattr(control, "_processes", lambda: [leftover])
    monkeypatch.setattr(control, "LEFTOVER_GRACE", 0)
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append(pid))
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 1
    assert killed == [20]
    assert "FAIL leftover process 20" in (run / "artifacts" / "down.txt").read_text("utf-8")


def test_teardown_stops_every_operator_the_run_recorded(run, monkeypatch):
    _record_operator(run, "op-11111111", "lead")
    _record_operator(run, "op-22222222", "scout", parent="op-11111111")
    exe = control._venv_exe(run, "operator")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    stopped = []
    monkeypatch.setattr(control, "_invoke",
                        lambda run_, label, argv, cwd: stopped.append(argv[1:]) or 0)
    control.cmd_down(SimpleNamespace(run=str(run)))
    assert sorted(stopped) == [["stop", "op-11111111"], ["stop", "op-22222222"]]


def test_addressing_a_run_that_was_never_created_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control._meta(tmp_path / "nope")


@pytest.mark.parametrize("verb", ["cmd_operator", "cmd_exec", "cmd_keys", "cmd_screen",
                                  "cmd_wait"])
def test_driving_a_run_after_down_is_refused_not_a_traceback(run, verb):
    _record_operator(run, "op-11111111", "lead")
    control.cmd_down(SimpleNamespace(run=str(run)))
    with pytest.raises(SystemExit, match="is down"):
        getattr(control, verb)(SimpleNamespace(
            run=str(run), cwd=None, rest=["list"], label="x", hold=False,
            target="lead", text=None, keys=["Enter"], file=None, screen="lead",
            contains="x", timeout=0))
