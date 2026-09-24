"""Recovering the seats a crash or a reboot took down.

A seat is not a process. It is an identity with a journal, a handoff and a
session number that accumulate over a project's life, and that is the whole
point of the thing: the longer a seat works somewhere, the more it knows about
it. Losing the machine should cost it the process, not the continuity.

Before this there was no way to get it back. `active_instances` asks who is
here *now*, and after a reboot the answer is nobody -- the multiplexer server
is gone and every supervisor pid belongs to a previous boot -- so
`restart_all_loops`, the sweep meant for exactly this shape of problem, found
nothing to restart. Everything needed was already on disk and nothing read it.

The discriminator is what a clean stop leaves behind, which is nothing:
`cleanup_files` removes the ownership claim and the recorded loop arguments. A
crash removes neither. That is why "was it stopped on purpose?" needs no flag
anybody has to remember to set, and the tests below are mostly about keeping
those two cases apart -- because the cost of confusing them is either a seat
that never comes back, or one that resurrects after a human deliberately
retired it.
"""
from __future__ import annotations

import json

import pytest

import op
from conftest import FakeMux
from supervisor_control import launch_status, recover_loop, recoverable_instances


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


def _crashed(name: str, workdir, *, args=("--agent", "test:agent")):
    """A seat exactly as an unplanned shutdown leaves it.

    Both files matter and for different reasons: the ownership claim is what
    makes it a *managed* instance at all, and the recorded arguments are what
    it can be restarted with.
    """
    inst = op.Instance(name)
    inst.managed_file.parent.mkdir(parents=True, exist_ok=True)
    inst.managed_file.write_text(json.dumps({"session": inst.session}),
                                 encoding="utf-8")
    inst.loop_args_file.write_text(
        json.dumps({"user_args": list(args), "cwd": str(workdir)}),
        encoding="utf-8")
    return inst


# ── who needs recovering ─────────────────────────────────────────


def test_a_seat_that_was_running_when_the_machine_died_is_recoverable(home):
    _crashed("crashed", home / "work")
    assert [i.display_name for i in recoverable_instances()] == ["crashed"]


def test_a_seat_stopped_on_purpose_is_not_recoverable(home):
    """`cleanup_files` is the whole mechanism, and this is the test of it.

    A seat somebody retired must not come back when the machine next boots.
    There is no flag for this: the absence of the files a clean stop removes
    *is* the record that it was stopped cleanly.
    """
    inst = _crashed("retired", home / "work")
    inst.cleanup_files()
    assert recoverable_instances() == []


def test_a_seat_with_a_live_session_is_not_recoverable(home):
    """It did not need recovering; starting a second supervisor for it is the
    two-supervisors catastrophe by another route."""
    inst = _crashed("still-up", home / "work")
    op.MUX.sessions[inst.session] = {"cwd": "", "argv": [],
                                     "remain_on_exit": True, "dead": False}
    assert recoverable_instances() == []


def test_a_seat_whose_supervisor_is_alive_is_not_recoverable(home, monkeypatch):
    """A loop between sessions has no session for a moment, and is fine."""
    _crashed("between-sessions", home / "work")
    monkeypatch.setattr(op.supervisor_control, "_running_loop_pid",
                        lambda instance: 4242)
    assert recoverable_instances() == []


def test_a_seat_with_no_recorded_arguments_is_not_offered(home):
    """There is nothing to restart it *with*, so offering it would be a lie.

    It is reported by `recover_loop` if named directly, which is where a human
    asking about one specific seat should hear it.
    """
    inst = op.Instance("argless")
    inst.managed_file.parent.mkdir(parents=True, exist_ok=True)
    inst.managed_file.write_text("{}", encoding="utf-8")
    assert recoverable_instances() == []


def test_several_seats_are_listed_in_a_stable_order(home):
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
        lambda inst, args, is_fresh, adopt=False, cwd=None: calls.append(
            {"name": inst.display_name, "args": args, "fresh": is_fresh,
             "adopt": adopt, "cwd": cwd}) or 4242)
    return calls


def test_recovering_continues_the_run_rather_than_starting_a_new_one(
        home, spawned):
    """`--fresh` would restart the session numbering, discard the resume id and
    re-arm the breakers. The seat's continuity is the entire feature, so this
    is the assertion the command exists to satisfy."""
    inst = _crashed("continues", home / "work")
    assert recover_loop(inst) == 0
    assert spawned[0]["fresh"] is False


def test_recovering_does_not_adopt(home, spawned):
    """Adoption joins a session that is still running, and after a crash there
    is none. Adopting nothing refuses, so a supervisor spawned with it would
    die on startup."""
    inst = _crashed("no-adopt", home / "work")
    recover_loop(inst)
    assert spawned[0]["adopt"] is False


def test_the_seat_is_recovered_where_it_was_working(home, spawned):
    """Its journal and handoff are keyed to that project. Starting it in the
    caller's directory would point the seat at a different one."""
    inst = _crashed("in-place", home / "work")
    recover_loop(inst)
    assert spawned[0]["cwd"] == str(home / "work")


def test_the_recorded_arguments_are_the_ones_it_comes_back_with(home, spawned):
    inst = _crashed("same-args", home / "work",
                    args=("--agent", "kernel:seat", "--effort", "high"))
    recover_loop(inst)
    assert spawned[0]["args"] == ["--agent", "kernel:seat", "--effort", "high"]


# ── refusals ─────────────────────────────────────────────────────


def test_a_seat_with_no_recorded_arguments_is_refused(home, spawned):
    inst = op.Instance("argless")
    inst.managed_file.parent.mkdir(parents=True, exist_ok=True)
    inst.managed_file.write_text("{}", encoding="utf-8")
    assert recover_loop(inst) == 1
    assert spawned == []


def test_a_seat_whose_project_is_gone_is_refused(home, spawned, tmp_path):
    """Recovering it elsewhere would silently point it at another project."""
    missing = tmp_path / "deleted-project"
    inst = _crashed("homeless", missing)
    assert recover_loop(inst) == 1
    assert spawned == [], "a seat was recovered into a directory that is gone"


def test_a_seat_that_is_already_running_is_refused(home, spawned):
    """Between listing and acting, something may have started it."""
    inst = _crashed("already-up", home / "work")
    op.MUX.sessions[inst.session] = {"cwd": "", "argv": [],
                                     "remain_on_exit": True, "dead": False}
    assert recover_loop(inst) == 1
    assert spawned == []


def test_a_spawn_that_fails_is_reported_rather_than_raised(home, monkeypatch):
    """One seat that cannot start must not take the sweep down with it."""
    def explode(*a, **k):
        raise OSError("no processes available")

    monkeypatch.setattr(op.supervisor_control, "_spawn_background_loop", explode)
    inst = _crashed("unspawnable", home / "work")
    assert recover_loop(inst) == 1


def test_a_missing_session_names_operator_start(home, capsys):
    from supervisor_control import restart_loop
    assert restart_loop("ghost") == 1
    err = capsys.readouterr().err
    assert "operator start --name ghost" in err
    assert "operator --loop" not in err


def test_launch_status_is_dead_when_the_pid_is_gone(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: None)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == "dead"


def test_launch_status_is_ready_when_the_pid_file_is_there(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: 99)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == "ready"


def test_launch_status_is_starting_when_the_pid_file_has_not_landed(monkeypatch):
    import supervisor_control as sc
    monkeypatch.setattr(sc, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(sc, "_running_loop_pid", lambda inst: None)
    assert launch_status(op.Instance("alpha"), 99, timeout=0) == "starting"
