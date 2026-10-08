"""Recovering the operators a crash or a reboot took down.

The discriminator is what a clean stop leaves behind, which is nothing:
`cleanup_files` removes the ownership claim and the recorded loop arguments. A
crash removes neither.
"""
from __future__ import annotations

import json

import pytest

import op
from conftest import FakeMux
from supervisor_control import (launch_status, recover_loop,
                                recoverable_instances, wait_for_session)


@pytest.fixture
def home(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    mux = FakeMux()
    monkeypatch.setattr(op, "MUX", mux)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


def _record(inst, cwd) -> None:
    directory = op.OPERATOR_HOME / "operators"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{inst.id}.json").write_text(json.dumps({
        "id": inst.id,
        "name": inst.display_name,
        "cwd": str(cwd),
        "created": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")


def _crashed(name: str, workdir, *, args=("--agent", "test:agent")):
    """An operator exactly as an unplanned shutdown leaves it.

    The record is what makes it known. The recorded arguments are what it can
    be restarted with. A clean stop removes the arguments and leaves the record.
    """
    inst = op.Instance(name)
    _record(inst, workdir)
    inst.loop_args_file.parent.mkdir(parents=True, exist_ok=True)
    inst.loop_args_file.write_text(
        json.dumps({"user_args": list(args), "cwd": str(workdir)}),
        encoding="utf-8")
    return inst


# ── who needs recovering ─────────────────────────────────────────


def test_a_operator_that_was_running_when_the_machine_died_is_recoverable(home):
    _crashed("crashed", home / "work")
    assert [i.display_name for i in recoverable_instances()] == ["crashed"]


def test_a_operator_stopped_on_purpose_is_not_recoverable(home):
    """`cleanup_files` is the whole mechanism, and this is the test of it.

    An operator somebody retired must not come back when the machine next boots.
    There is no flag for this: the absence of the files a clean stop removes
    *is* the record that it was stopped cleanly.
    """
    inst = _crashed("retired", home / "work")
    inst.cleanup_files()
    assert (op.OPERATOR_HOME / "operators" / f"{inst.id}.json").is_file()
    assert recoverable_instances() == []


def test_a_operator_with_a_live_session_is_not_recoverable(home):
    """It did not need recovering; starting a second supervisor for it is the
    two-supervisors catastrophe by another route."""
    inst = _crashed("still-up", home / "work")
    op.MUX.sessions[inst.session] = {"cwd": "", "argv": [],
                                     "remain_on_exit": True, "dead": False}
    assert recoverable_instances() == []


def test_a_operator_whose_supervisor_is_alive_is_not_recoverable(home, monkeypatch):
    """A loop between sessions has no session for a moment, and is fine."""
    _crashed("between-sessions", home / "work")
    monkeypatch.setattr(op.supervisor_control, "_running_loop_pid",
                        lambda instance: 4242)
    assert recoverable_instances() == []


def test_a_operator_with_no_recorded_arguments_is_not_offered(home):
    """There is nothing to restart it *with*, so offering it would be a lie.

    It is reported by `recover_loop` if named directly, which is where a human
    asking about one specific operator should hear it.
    """
    inst = op.Instance("argless")
    _record(inst, home / "work")
    assert recoverable_instances() == []


def test_several_operators_are_listed_in_a_stable_order(home):
    """A reboot takes the whole fleet, so this is the normal case, not the
    exotic one."""
    for name in ("charlie", "alpha", "bravo"):
        _crashed(name, home / "work")
    assert [i.display_name for i in recoverable_instances()] == [
        "alpha", "bravo", "charlie"]


# ── what recovering one does ─────────────────────────────────────


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(
        op.supervisor_control, "_spawn_background_loop",
        lambda inst, args, is_fresh, cwd=None: calls.append(
            {"name": inst.display_name, "args": args, "fresh": is_fresh,
             "cwd": cwd}) or 4242)
    return calls


def test_recovering_continues_the_run_rather_than_starting_a_new_one(
        home, spawned):
    """`--fresh` would restart the session numbering and discard the resume id."""
    inst = _crashed("continues", home / "work")
    assert recover_loop(inst) == 0
    assert spawned[0]["fresh"] is False


def test_the_operator_is_recovered_where_it_was_working(home, spawned):
    """Starting it in the caller's directory would point the operator at a
    different project than the one it recorded."""
    inst = _crashed("in-place", home / "work")
    recover_loop(inst)
    assert spawned[0]["cwd"] == str(home / "work")


def test_the_recorded_arguments_are_the_ones_it_comes_back_with(home, spawned):
    inst = _crashed("same-args", home / "work",
                    args=("--agent", "kernel:operator", "--effort", "high"))
    recover_loop(inst)
    assert spawned[0]["args"] == ["--agent", "kernel:operator", "--effort", "high"]


# ── refusals ─────────────────────────────────────────────────────


def test_a_operator_with_no_recorded_arguments_is_refused(home, spawned):
    inst = op.Instance("argless")
    inst.managed_file.parent.mkdir(parents=True, exist_ok=True)
    inst.managed_file.write_text("{}", encoding="utf-8")
    assert recover_loop(inst) == 1
    assert spawned == []


def test_a_operator_whose_project_is_gone_is_refused(home, spawned, tmp_path):
    """Recovering it elsewhere would silently point it at another project."""
    missing = tmp_path / "deleted-project"
    inst = _crashed("homeless", missing)
    assert recover_loop(inst) == 1
    assert spawned == [], "an operator was recovered into a directory that is gone"


def test_a_operator_that_is_already_running_is_refused(home, spawned):
    """Between listing and acting, something may have started it."""
    inst = _crashed("already-up", home / "work")
    op.MUX.sessions[inst.session] = {"cwd": "", "argv": [],
                                     "remain_on_exit": True, "dead": False}
    assert recover_loop(inst) == 1
    assert spawned == []


def test_a_spawn_that_fails_is_reported_rather_than_raised(home, monkeypatch):
    """One operator that cannot start must not take the sweep down with it."""
    def explode(*a, **k):
        raise OSError("no processes available")

    monkeypatch.setattr(op.supervisor_control, "_spawn_background_loop", explode)
    inst = _crashed("unspawnable", home / "work")
    assert recover_loop(inst) == 1


def test_launch_status_is_dead_when_the_pid_is_gone(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: None)
    monkeypatch.setattr(sc, "_supervisor_present", lambda inst: False)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == ("dead", 99)


def test_launch_status_is_ready_when_the_pid_file_is_there(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: 77)
    monkeypatch.setattr(sc, "_supervisor_present", lambda inst: True)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == ("ready", 77)


def test_launch_status_is_unknown_when_the_pid_file_has_not_landed(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: None)
    monkeypatch.setattr(sc, "_supervisor_present", lambda inst: False)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == ("unknown", 99)


def test_wait_for_session_returns_when_the_mux_has_it(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc.MUX, "available", lambda: True)
    monkeypatch.setattr(sc.MUX, "has_session", lambda session: True)
    assert wait_for_session(op.Instance("alpha"), timeout=0) is True


def test_wait_for_session_times_out_when_the_session_never_appears(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc.MUX, "available", lambda: True)
    monkeypatch.setattr(sc.MUX, "has_session", lambda session: False)
    assert wait_for_session(op.Instance("alpha"), timeout=0) is False


def test_launch_status_ignores_the_parents_startup_record(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: None)
    monkeypatch.setattr(sc, "_supervisor_present", lambda inst: True)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == ("dead", 99)


def test_stopping_several_marks_every_one_before_waiting_on_any(home, monkeypatch):
    import supervisor_control
    family = [op.Instance(name) for name in ("lead", "scout", "deep")]
    marked_at_each_wait = []

    def wait(inst):
        marked_at_each_wait.append(
            (inst.id, {each.id for each in family if each.stop_marker.exists()}))

    monkeypatch.setattr(supervisor_control, "_request_supervisor_stop", wait)
    supervisor_control.stop_all(family)
    everyone = {each.id for each in family}
    assert sorted(marked_at_each_wait) == sorted((each.id, everyone) for each in family)


def test_stopping_several_waits_on_all_of_them_at_once(home, monkeypatch):
    """Each wait can take a whole stop budget, so waiting in turn would multiply it."""
    import threading
    import supervisor_control
    together = threading.Barrier(3, timeout=5)
    monkeypatch.setattr(supervisor_control, "_request_supervisor_stop",
                        lambda inst: together.wait())
    supervisor_control.stop_all([op.Instance(name) for name in ("lead", "scout", "deep")])
    assert not together.broken
