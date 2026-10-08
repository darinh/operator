"""The supervisor writes a progress_verdict after each completed session.

The recorder in evidence.py can be correct and still never run. These tests
drive `run_loop_mode` so a deleted call site cannot hide behind a green
unit test of the recorder.
"""
from __future__ import annotations

import json

import pytest

import op


@pytest.fixture
def looping(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "COPILOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    monkeypatch.setattr(op, "LAUNCH_BACKOFF_BASE", 0)
    monkeypatch.setattr(op, "RESTART_PAUSE_SECONDS", 0)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


def _age_clock(monkeypatch):
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])
    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)


def _one_then_stop(attempts, fingerprints=None):
    def start_session(instance, args, session_num, remain_on_exit=False,
                      preamble=""):
        attempts["n"] += 1
        if fingerprints is not None and attempts["n"] == 1:
            fingerprints["value"] = "moved"
        if attempts["n"] >= 2:
            instance.stop_marker.touch()
        else:
            instance.restart_marker.write_text(
                json.dumps({"id": instance.id, "session": session_num}),
                encoding="utf-8")
    return start_session


def _verdicts(home):
    path = op.evidence.trace_path(home)
    if not path.exists():
        return []
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines()]
    return [r for r in records if r.get("event") == "progress_verdict"]


RESUME_ID = "3f2a9c1e-1111-2222-3333-444455556666"


@pytest.mark.parametrize("tail", [
    ["--", "some text"],
    ["--", "--resume=literal"],
    ["--", "--log-level=info"],
])
def test_generated_options_ignore_the_literal_tail(looping, monkeypatch, tail):
    """run_loop_mode -> start_session, not the helper. Three tails from review."""
    import launch
    captured = []

    def save(inst, argv, cwd, n):
        captured.append(list(argv))
        inst.stop_marker.touch()
        return inst.spec_file

    monkeypatch.setattr(launch, "write_launch_spec", save)
    monkeypatch.setattr(launch, "copilot_executable", lambda: "copilot")
    monkeypatch.setattr(op.Instance, "copilot_pid", lambda self: 1)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    inst = op.Instance("plain")
    inst.save_state(1, "2026-01-01T00:00:00Z", RESUME_ID)
    op.run_loop_mode(inst, tail, is_fresh=False)
    assert captured, "start_session never wrote a launch spec"
    argv = captured[0]
    assert "--" not in argv
    assert f"--resume={RESUME_ID}" in argv
    assert tail[-1] not in argv
    assert argv[argv.index("-i") + 1].endswith(f" Task: {tail[-1]}")
    assert argv[argv.index("--log-level") + 1] == "debug"


def test_resume_is_threaded_through_before_terminator():
    from pathlib import Path
    source = Path(op.supervisor.__file__).read_text(encoding="utf-8")
    assert "before_terminator(" in source
    assert 'launch_args.append(f"--resume' not in source


def test_the_loop_takes_a_fresh_flag_and_nothing_beside_it():
    """A fourth mode flag used to mean take over a live session. The spawn
    list is the contract the child parser still has to accept."""
    import inspect
    assert tuple(inspect.signature(op.run_loop_mode).parameters) == (
        "instance", "user_args", "is_fresh")
    assert tuple(inspect.signature(op._spawn_background_loop).parameters) == (
        "instance", "copilot_args", "is_fresh", "cwd")


@pytest.mark.parametrize("reported, stranded", [(False, False), (True, False), (True, True)])
def test_mail_is_typed_only_once_the_session_reports_its_id(
        looping, monkeypatch, reported, stranded):
    """Keystrokes sent before a launch's session is up are lost, so the
    supervisor holds mail until the runner has written this launch's id.
    Mail a killed supervisor had claimed but not typed goes out on restart."""
    inst = op.Instance("reader")
    op.mail.post(inst.id, {"from": "human", "from_name": "human", "to": inst.id,
                           "relation": "your parent", "text": "ping",
                           "sent": "2026-01-02T03:04:05Z"})
    if stranded:
        assert op.mail.take(inst.id) is not None
        assert op.mail.waiting(inst.id) == 0
    polls = {"n": 0}

    def start_session(instance, args, session_num, remain_on_exit=False, preamble=""):
        op.MUX.sessions[instance.session] = {"cwd": "", "argv": [],
                                             "remain_on_exit": False, "dead": False}
        if reported:
            instance.session_file.write_text(RESUME_ID, encoding="utf-8")

    def running(instance):
        polls["n"] += 1
        if polls["n"] >= 2:
            instance.stop_marker.touch()
        return True

    monkeypatch.setattr(op, "start_session", start_session)
    monkeypatch.setattr(op, "is_copilot_running", running)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    monkeypatch.setattr(op, "SESSION_ID_WAIT", 0)
    assert op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True) == 0
    typed = [text for _, text in op.MUX.keys if text.startswith("[operator message")]
    assert typed == (["[operator message from the person who started you] ping"]
                     if reported else [])
    assert op.mail.waiting(inst.id) == (0 if reported else 1)


