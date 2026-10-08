"""Tests for the fake copilot a verify-operator run installs.

The fake stands where Copilot stands, so each thing the kernel expects of
Copilot is checked against the kernel's own code here: that the runner can read
the session id it writes, and that it reads its own name out of the preamble
the kernel really renders. A fake that drifts from either makes a live pass
prove nothing while looking green.

Run with::

    python -m pytest .github/skills/verify-operator -q
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SKILL = Path(__file__).resolve().parent
_KERNEL = _SKILL.parents[2] / "operator_kernel"
_spec = importlib.util.spec_from_file_location("verify_operator_fake",
                                               _SKILL / "fake_copilot.py")
fake = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fake
_spec.loader.exec_module(fake)

PREAMBLE = ("You are operator scout (op-1a2b3c4d). Operator lead (op-9f8e7d6c) "
            "started you and is your parent. Task: role=scout")


class _Idle(Exception):
    """Raised where the fake would otherwise wait forever."""


def _never_idle(seconds):
    raise _Idle(seconds)


@pytest.fixture
def run(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "run"
    (directory / "artifacts" / "scripts").mkdir(parents=True)
    monkeypatch.setenv("FAKE_RUN", str(directory))
    monkeypatch.setenv("COPILOT_LOG_DIR", str(directory / "logs"))
    monkeypatch.delenv("FAKE_PATH_FIRST", raising=False)
    return directory


def _script(run: Path, name: str, steps: list, session: int | None = None) -> None:
    suffix = f".s{session}" if session else ""
    (run / "artifacts" / "scripts" / f"{name}{suffix}.json").write_text(
        json.dumps(steps), encoding="utf-8")


def _session(monkeypatch, argv: list[str], stdin: str = "") -> None:
    """One launch of the fake, until it would sit idle."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(fake.time, "sleep", _never_idle)
    with pytest.raises(_Idle):
        fake.main(argv)


# ── what the kernel expects of Copilot ───────────────────────────────────


def test_the_fake_reads_its_name_from_the_preamble_the_kernel_renders(tmp_path):
    """Rendered in a child process: the kernel reads its home at import."""
    work = tmp_path / "work"
    work.mkdir()
    render = ("import json, sys, operators, preamble\n"
              "lead = operators.create('lead', sys.argv[1])\n"
              "kid = operators.create('scout two', sys.argv[1], parent=lead.id)\n"
              "print(json.dumps([kid.id, preamble.build_preamble(kid.instance())]))\n")
    env = {**os.environ, "COPILOT_OPERATOR_HOME": str(tmp_path / "home"),
           "PYTHONPATH": str(_KERNEL)}
    proc = subprocess.run([sys.executable, "-c", render, str(work)], env=env,
                          capture_output=True, text=True, cwd=work)
    assert proc.returncode == 0, proc.stderr
    kid_id, text = json.loads(proc.stdout)
    assert fake.identity(text) == ("scout two", kid_id)


def test_the_runner_finds_the_session_id_the_fake_writes(tmp_path, monkeypatch):
    """Mail is typed into a session only once the runner has its id, so a fake
    whose log the runner cannot read silently disables delivery."""
    monkeypatch.syspath_prepend(str(_KERNEL))
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location("verify_operator_runner",
                                                  _KERNEL / "runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    started = int(time.time() * 1000)
    session_id = "0123abcd-0000-4000-8000-0123456789ab"
    path = fake.write_process_log(tmp_path / "logs", session_id, 4242, started + 50)
    assert runner._find_log(tmp_path / "logs", {4242}, started) == path
    assert runner._extract_session_id(path) == session_id


def test_the_fake_reads_the_prompt_operator_passes():
    argv = ["--yolo", "--experimental", "-i", PREAMBLE, "--log-level", "debug"]
    assert fake.prompt_of(argv) == PREAMBLE
    assert fake.prompt_of(["-p", "once"]) == "once"
    assert fake.prompt_of(["--yolo"]) == ""


def test_text_without_a_preamble_is_unnamed():
    assert fake.identity("no preamble here") == ("unnamed", "")


def test_the_fake_answers_version_without_a_run(monkeypatch, capsys):
    """`doctor` asks it, and a real Copilot would answer differently."""
    monkeypatch.delenv("FAKE_RUN", raising=False)
    assert fake.main(["--version"]) == 0
    assert "verify-operator" in capsys.readouterr().out


def test_the_fake_refuses_to_run_outside_a_run(monkeypatch):
    monkeypatch.delenv("FAKE_RUN", raising=False)
    assert fake.main(["-i", PREAMBLE]) == 2


# ── steps ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("steps", [
    [],
    [{"op": ["start", "b", "role=b"]}, {"sleep": 0.5}, {"cd": "sub"}, {"exit": 0}],
    [{"on": "ping", "do": [{"op": ["send", "lead", "pong"]}]}],
])
def test_every_step_kind_is_accepted(steps):
    fake.validate_steps(steps)


@pytest.mark.parametrize("steps", [
    {"op": ["list"]},
    [{"run": ["list"]}],
    [{"op": "list"}],
    [{"op": ["list", 3]}],
    [{"op": ["list"], "sleep": 1}],
    [{"sleep": "1"}],
    [{"cd": 3}],
    [{"exit": "0"}],
    [{"on": ""}],
    [{"on": "ping", "do": [{"bogus": 1}]}],
    [{"sleep": 1, "do": []}],
    ["just text"],
])
def test_a_step_the_fake_cannot_run_is_refused(steps):
    with pytest.raises(ValueError):
        fake.validate_steps(steps)


def test_a_session_script_wins_over_the_default(tmp_path):
    (tmp_path / "lead.json").write_text("[]", encoding="utf-8")
    (tmp_path / "lead.s2.json").write_text("[]", encoding="utf-8")
    assert fake.script_for(tmp_path, "lead", 1).name == "lead.json"
    assert fake.script_for(tmp_path, "lead", 2).name == "lead.s2.json"
    assert fake.script_for(tmp_path, "nobody", 1) is None


# ── a session ────────────────────────────────────────────────────────────


def test_a_session_runs_its_script_as_operator_and_logs_it(run, monkeypatch):
    _script(run, "scout", [{"op": ["list"]}])
    calls = []

    def operator(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="  1. scout\n", stderr="")

    monkeypatch.setattr(fake.shutil, "which", lambda name: f"/venv/{name}")
    monkeypatch.setattr(fake.subprocess, "run", operator)
    _session(monkeypatch, ["-i", PREAMBLE])

    assert [argv for argv, _ in calls] == [["/venv/operator", "list"]]
    assert calls[0][1]["stdin"] is fake.subprocess.DEVNULL
    log = (run / "artifacts" / "agents" / "scout" / "commands.log").read_text("utf-8")
    assert "$ operator list" in log and "1. scout" in log
    assert "exit 0 after " in log, "a slow verb is invisible without its duration"


def test_a_typed_exit_ends_the_session_as_copilot_does(run, monkeypatch):
    """Stop and handoff type `/exit`. A fake that ignores it makes every stop
    wait out the kernel's grace period and take the kill path instead."""
    _script(run, "scout", [{"on": "ping", "do": [{"exit": 3}]}])
    monkeypatch.setattr(sys, "stdin", io.StringIO("/exit\nping\n"))
    monkeypatch.setattr(fake.time, "sleep", _never_idle)
    with pytest.raises(SystemExit) as ended:
        fake.main(["-i", PREAMBLE])
    assert ended.value.code == 0
    lines = (run / "artifacts" / "agents" / "scout" / "stdin.log").read_text("utf-8")
    assert lines.rstrip().endswith("/exit")


def test_each_launch_is_a_new_session_with_its_own_script(run, monkeypatch):
    """A handoff relaunches the same operator, and session 2 must not repeat
    session 1's steps, or a handoff loops forever."""
    _script(run, "scout", [{"cd": "."}])
    _script(run, "scout", [{"exit": 7}], session=2)
    _session(monkeypatch, ["-i", PREAMBLE])
    with pytest.raises(SystemExit) as ended:
        fake.main(["-i", PREAMBLE + " second"])
    assert ended.value.code == 7

    agent = run / "artifacts" / "agents" / "scout"
    starts = agent.joinpath("starts.log").read_text("utf-8").splitlines()
    assert ["session=1" in starts[0], "session=2" in starts[1]] == [True, True]
    assert "script=scout.json" in starts[0] and "script=scout.s2.json" in starts[1]
    assert "id=op-1a2b3c4d" in starts[0]
    assert agent.joinpath("prompt-2.txt").read_text("utf-8").endswith("second\n")


def test_a_session_leaves_the_log_the_runner_reads(run, monkeypatch):
    _session(monkeypatch, ["-i", PREAMBLE])
    (log,) = (run / "logs").glob("process-*.log")
    assert log.name.endswith(f"-{os.getpid()}.log")
    starts = (run / "artifacts" / "agents" / "scout" / "starts.log").read_text("utf-8")
    assert json.loads(log.read_text("utf-8").splitlines()[1].split(" ", 2)[2]) == {
        "session_id": starts.split("session_id=")[1].split()[0]}


def test_typed_lines_are_logged_and_trigger_their_handlers(run, monkeypatch):
    _script(run, "scout", [{"on": "ping", "do": [{"op": ["send", "lead", "pong"]}]}])
    sent = []
    monkeypatch.setattr(fake.shutil, "which", lambda name: name)
    monkeypatch.setattr(fake.subprocess, "run", lambda argv, **kw: sent.append(argv)
                        or SimpleNamespace(returncode=0, stdout="", stderr=""))
    typed = "[operator message from lead (op-9f8e7d6c), your parent] ping\nhello\n"
    _session(monkeypatch, ["-i", PREAMBLE], stdin=typed)

    assert sent == [["operator", "send", "lead", "pong"]]
    lines = (run / "artifacts" / "agents" / "scout" / "stdin.log").read_text("utf-8")
    assert "your parent] ping" in lines and lines.rstrip().endswith("hello")


def test_the_pane_finds_the_runs_operator_first(run, monkeypatch):
    """psmux hands a pane the registry PATH, which holds a different operator."""
    monkeypatch.setenv("FAKE_PATH_FIRST", "/run/venv/bin")
    monkeypatch.setenv("PATH", "/usr/bin")
    _session(monkeypatch, ["-i", PREAMBLE])
    assert os.environ["PATH"].split(os.pathsep)[0] == "/run/venv/bin"
