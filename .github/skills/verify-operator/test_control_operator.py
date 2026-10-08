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


def test_no_session_server_keeps_a_warm_server_in_the_run(run):
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


def test_doctor_checks_both_scripts_a_run_installs():
    """A run that resolves `operator` but not `copilot` launches a real one."""
    assert set(control.DRIVEN_SCRIPTS) == {"operator", "copilot"}
    assert 'copilot = "fake_copilot:main"' in control.FAKE_PYPROJECT


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


def test_a_verb_never_waits_on_the_callers_console(monkeypatch, run):
    """`operator delete` asks a question when it has a terminal."""
    seen = {}

    def spy(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(control.subprocess, "run", spy)
    control._invoke(run, "x", ["operator", "list"], run)
    assert seen["stdin"] is control.subprocess.DEVNULL


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
    assert pane[1:] == [str(Path(control.__file__).resolve()), "exec", "--run", str(run)]
    assert args[args.index("-s") + 1] == "vo-test"
    assert args[args.index("-c") + 1] == str(run / "repo")
    assert env["PATH"].split(os.pathsep)[0] == str(control._scripts_dir(run))


def test_a_target_is_the_menu_or_an_operator_by_name_or_id(run):
    _record_operator(run, "op-11111111", "lead")
    assert control._session(run, "menu") == "vo-test"
    assert control._session(run, "lead") == "op-11111111"
    assert control._session(run, "op-11111111") == "op-11111111"
    with pytest.raises(SystemExit):
        control._session(run, "nobody")


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
    for name in control.INSTANCE_DIRS:
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
