"""The supervisor accepts a restart only when the marker names this session."""
from __future__ import annotations

import json

import pytest

import op


def _loop(monkeypatch, tmp_path):
    restart = tmp_path / "restart"
    restart.mkdir()
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "COPILOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    monkeypatch.setattr(op, "LAUNCH_BACKOFF_BASE", 0)
    monkeypatch.setattr(op, "RESTART_PAUSE_SECONDS", 0)
    monkeypatch.setattr(op, "SESSION_ID_WAIT", 0)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path / "operator.log"


def _log(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_supervisor_restarts_on_a_claim_for_this_session(monkeypatch, tmp_path):
    log_path = _loop(monkeypatch, tmp_path)
    seen = []

    def start(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(session_num)
        if len(seen) == 1:
            assert op.exits.request_restart(instance.id, session_num)
        else:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    monkeypatch.setattr(op, "start_session", start)
    inst = op.Instance("claimed")
    assert op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True) == 0
    assert seen == [1, 2]
    text = _log(log_path)
    assert "restart signal detected" in text
    assert "exited unexpectedly" not in text


def _ignored(monkeypatch, tmp_path, write, *, running_first=False):
    log_path = _loop(monkeypatch, tmp_path)
    seen = []
    if running_first:
        polls = {"n": 0}

        def running(_instance):
            polls["n"] += 1
            return polls["n"] == 1

        monkeypatch.setattr(op, "is_copilot_running", running)

    def start(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(session_num)
        if len(seen) == 1:
            write(instance, session_num)
        instance.exit_file.write_text("0", encoding="utf-8")

    monkeypatch.setattr(op, "start_session", start)
    inst = op.Instance("ignored")
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)
    return rc, seen, _log(log_path)


def test_a_marker_for_another_operator_is_an_unrequested_exit(monkeypatch, tmp_path):
    def write(instance, session_num):
        instance.restart_marker.write_text(
            json.dumps({"id": "other", "session": session_num}),
            encoding="utf-8")

    rc, seen, text = _ignored(monkeypatch, tmp_path, write)
    assert rc == 1
    assert seen == [1, 2, 3, 4, 5]
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text
    assert "exited unexpectedly" in text


def test_a_marker_for_the_previous_session_is_an_unrequested_exit(
        monkeypatch, tmp_path):
    def write(instance, session_num):
        instance.restart_marker.write_text(
            json.dumps({"id": instance.id, "session": session_num - 1}),
            encoding="utf-8")

    rc, seen, text = _ignored(monkeypatch, tmp_path, write)
    assert rc == 1
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text


def test_an_empty_marker_is_an_unrequested_exit(monkeypatch, tmp_path):
    def write(instance, session_num):
        instance.restart_marker.touch()

    rc, _seen, text = _ignored(monkeypatch, tmp_path, write)
    assert rc == 1
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text


def test_a_garbage_marker_is_an_unrequested_exit(monkeypatch, tmp_path):
    def write(instance, session_num):
        instance.restart_marker.write_text("not json", encoding="utf-8")

    rc, _seen, text = _ignored(monkeypatch, tmp_path, write)
    assert rc == 1
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text


@pytest.mark.parametrize("fake", [True, 1.0])
def test_a_session_that_only_equals_the_number_is_an_unrequested_exit(
        monkeypatch, tmp_path, fake):
    """JSON `true` and `1.0` compare equal to 1 in Python. Neither is a session."""
    def write(instance, session_num):
        assert session_num == 1
        instance.restart_marker.write_text(
            json.dumps({"id": instance.id, "session": fake}),
            encoding="utf-8")

    rc, _seen, text = _ignored(monkeypatch, tmp_path, write)
    assert rc == 1
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text


def test_a_wrong_claim_while_copilot_is_up_does_not_restart(monkeypatch, tmp_path):
    """The poll that sees the marker before the exit must apply the same rule."""
    def write(instance, session_num):
        instance.restart_marker.write_text(
            json.dumps({"id": "other", "session": session_num}),
            encoding="utf-8")

    rc, seen, text = _ignored(monkeypatch, tmp_path, write, running_first=True)
    assert rc == 1
    assert seen[0] == 1
    assert "restart signal detected" not in text
    assert "Ignoring restart marker" in text
