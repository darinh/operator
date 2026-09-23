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
    monkeypatch.setattr(op, "TABS_FILE", tmp_path / "tabs.json")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    monkeypatch.setattr(op, "LAUNCH_BACKOFF_BASE", 0)
    monkeypatch.setattr(op, "RESTART_PAUSE_SECONDS", 0)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(op, "workspace_fingerprint", lambda cwd: "unmoving")
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
            instance.restart_marker.touch()
    return start_session


def _verdicts(home):
    path = op.evidence.trace_path(home)
    if not path.exists():
        return []
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines()]
    return [r for r in records if r.get("event") == "progress_verdict"]


def test_run_loop_mode_records_an_unchanged_progress_verdict(looping, monkeypatch):
    _age_clock(monkeypatch)
    attempts = {"n": 0}
    monkeypatch.setattr(op, "start_session", _one_then_stop(attempts))
    inst = op.Instance("a.b")
    assert inst.id != inst.display_name
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)
    records = _verdicts(looping)
    assert records, "run_loop_mode wrote no progress_verdict"
    rec = records[0]
    assert rec["verdict"] == "unchanged"
    assert rec["before"] == "unmoving"
    assert rec["after"] == "unmoving"
    assert rec["accounted"] is True
    assert rec["session_num"] == 1
    assert rec["nochange_streak"] == 1
    assert rec["unaccounted_streak"] == 0
    assert rec["limit_nochange"] == op.MAX_NOCHANGE_SESSIONS
    assert rec["limit_unaccounted"] == op.MAX_UNACCOUNTED_SESSIONS
    assert rec["instance"] == inst.id
    assert rec["instance"] != inst.display_name


def test_a_ledger_written_by_the_real_supervisor_verifies(looping, monkeypatch):
    from ledger_chain import Verified, verify

    op.evidence._chain_writer = None
    _age_clock(monkeypatch)
    attempts = {"n": 0}
    monkeypatch.setattr(op, "start_session", _one_then_stop(attempts))
    op.run_loop_mode(op.Instance("a.b"), ["--agent", "test:agent"], is_fresh=True)
    result = verify([op.evidence.trace_path(looping)])
    assert isinstance(result, Verified)
    assert result.records >= 1
    assert result.writers == 1


def test_run_loop_mode_records_a_changed_progress_verdict(looping, monkeypatch):
    _age_clock(monkeypatch)
    attempts = {"n": 0}
    fingerprints = {"value": "unmoving"}
    monkeypatch.setattr(op, "workspace_fingerprint",
                        lambda cwd: fingerprints["value"])
    monkeypatch.setattr(op, "start_session",
                        _one_then_stop(attempts, fingerprints))
    inst = op.Instance("a.b")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)
    records = _verdicts(looping)
    assert records, "run_loop_mode wrote no progress_verdict"
    rec = records[0]
    assert rec["verdict"] == "changed"
    assert rec["before"] == "unmoving"
    assert rec["after"] == "moved"
    assert rec["nochange_streak"] == 0
    assert rec["unaccounted_streak"] == 0
    assert rec["instance"] == inst.id


def test_seat_watch_ignores_progress_verdict_from_the_live_loop(looping, monkeypatch):
    from operator_extensions import activation, seat_watch

    _age_clock(monkeypatch)
    attempts = {"n": 0}
    monkeypatch.setattr(op, "start_session", _one_then_stop(attempts))
    inst = op.Instance("a.b")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)
    records = [json.loads(line) for line in
               op.evidence.trace_path(looping).read_text(encoding="utf-8").splitlines()]
    verdicts = [r for r in records if r.get("event") == "progress_verdict"]
    assert verdicts
    (looping / activation.CONFIG_NAME).write_text(
        json.dumps({seat_watch.NAME: {"enabled": True, "failures": 1}}),
        encoding="utf-8")
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(looping))
    bait = dict(verdicts[0])
    bait["consecutive"] = 9
    bait["giving_up"] = True
    seat_watch.on_fact(facts=verdicts + [bait])
    assert activation.read_state(seat_watch.NAME).get("seats", {}) == {}


def test_resume_is_threaded_through_before_terminator():
    from pathlib import Path
    source = Path(op.supervisor.__file__).read_text(encoding="utf-8")
    assert "before_terminator(" in source
    assert 'launch_args.append(f"--resume' not in source


def test_a_start_without_an_agent_does_not_inject_anvil(looping, monkeypatch):
    """Stock Copilot CLI has no anvil:anvil agent. Injecting one crash-loops."""
    seen = []

    def start_session(instance, args, session_num, remain_on_exit=False,
                      preamble=""):
        seen.append(list(args))
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", start_session)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    op.run_loop_mode(op.Instance("plain"), [], is_fresh=True)
    assert seen, "the loop never launched"
    assert "--agent" not in seen[0]
    assert "anvil:anvil" not in seen[0]
