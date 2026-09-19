"""Adoption: taking over a live session without disturbing it.

`run_loop_mode(..., adopt=True)` is how a supervisor is *replaced* — to pick up
new operator code, say — while the Copilot session it was watching keeps
running. The session survives; only the process supervising it changes.

None of it was tested. The whole path, including its three refusals, had no
coverage anywhere in the suite, and two of those refusals exist to prevent the
same catastrophic shape: **two supervisors watching one session**. They would
relaunch over each other indefinitely, each seeing a session it did not start
and killing it. `supervisor.py` calls the pid check "the last line of defence"
against exactly that.

The refusals are the interesting half, because each is a case where doing
nothing is the correct outcome and doing something is unrecoverable:

* nothing to adopt -- the supervisor would poll a session that never appears;
* a session this operator does not own -- it would relaunch over somebody
  else's work;
* a supervisor already running -- the two would fight.

The behavioural half matters too, and is subtler: an adopted session keeps its
*own* number rather than moving to the next one, because no launch happened, and
the progress breaker re-arms rather than measuring, because this supervisor
never saw the repository state that session began with.
"""
from __future__ import annotations

import json

import pytest

import op
from conftest import FakeMux


@pytest.fixture
def adoptable(tmp_path, monkeypatch):
    """A supervisor home, with a mux whose sessions a test can populate."""
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
    monkeypatch.setattr(op, "SESSION_ID_WAIT", 0)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    mux = FakeMux()
    monkeypatch.setattr(op, "MUX", mux)
    return mux


def _own(instance, *, session: bool = True) -> None:
    """Record this operator's claim over the instance's session."""
    instance.managed_file.parent.mkdir(parents=True, exist_ok=True)
    claim = {"session": instance.session} if session else {"session": "someone-else"}
    instance.managed_file.write_text(json.dumps(claim), encoding="utf-8")


def _live(mux, instance) -> None:
    mux.sessions[instance.session] = {
        "cwd": "", "argv": [], "remain_on_exit": True, "dead": False}


def _refuse_before_supervising(monkeypatch) -> None:
    """Make "the refusal did not fire" fail immediately instead of hanging.

    Every refusal below happens *before* the supervision loop is entered, so
    nothing here should ever be called. Without this the tests still fail when
    a guard is removed -- but by spinning forever, because a supervisor that
    adopted something it should have refused then polls it indefinitely. That
    is precisely the catastrophe the guards exist to prevent, and it is a
    terrible way for a test to report: in CI it eats the job timeout instead
    of naming the defect. Measured: deleting either of the last two guards
    hung the run until it was killed at 90 seconds.
    """
    def never(*args, **kwargs):
        raise AssertionError(
            "the supervisor began supervising a session it should have "
            "refused to adopt")

    monkeypatch.setattr(op, "start_session", never)
    monkeypatch.setattr(op, "is_copilot_running", never)
    monkeypatch.setattr(op, "stop_session_gracefully", never)


# ── the three refusals ───────────────────────────────────────────


def test_adopting_a_session_that_is_not_there_refuses(adoptable, monkeypatch, capsys):
    """Otherwise the supervisor polls forever for a session nobody started.

    Asserted on the message, not merely on the exit. The ownership guard below
    also refuses this case -- with no session, `owns_live_session()` is false
    too -- so a test that only checked for `SystemExit` passed with this guard
    deleted. A mutation run found exactly that. The two refusals are not
    interchangeable: this one tells the operator there is nothing to adopt,
    and the other accuses them of adopting somebody else's work.
    """
    _refuse_before_supervising(monkeypatch)
    inst = op.Instance("absent")
    with pytest.raises(SystemExit) as exc:
        op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                         adopt=True)
    assert exc.value.code != 0
    assert "No running session" in capsys.readouterr().err


def test_adopting_a_session_this_operator_does_not_own_refuses(adoptable, monkeypatch, capsys):
    """A session with the right name is not the same as one we started.

    Without this the supervisor would adopt a stranger's session and, on its
    first unexpected exit, relaunch over it.
    """
    _refuse_before_supervising(monkeypatch)
    inst = op.Instance("not-ours")
    _live(adoptable, inst)
    # Live, but with no ownership claim recorded at all.
    with pytest.raises(SystemExit) as exc:
        op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                         adopt=True)
    assert exc.value.code != 0
    assert "not started by this operator" in capsys.readouterr().err


def test_a_claim_naming_another_session_does_not_authorise_adoption(adoptable, monkeypatch, capsys):
    """`ownership()` is checked against *this* session, not merely for presence."""
    _refuse_before_supervising(monkeypatch)
    inst = op.Instance("mismatched")
    _live(adoptable, inst)
    _own(inst, session=False)
    with pytest.raises(SystemExit) as exc:
        op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                         adopt=True)
    assert exc.value.code != 0
    assert "not started by this operator" in capsys.readouterr().err


def test_a_second_supervisor_refuses_to_start(adoptable, monkeypatch, capsys):
    """The last line of defence against two supervisors watching one session.

    They would relaunch over each other's sessions indefinitely, each one
    seeing a session it did not start. The handoff lock makes this unlikely;
    this makes it survivable.
    """
    _refuse_before_supervising(monkeypatch)
    inst = op.Instance("contested")
    _live(adoptable, inst)
    _own(inst)
    # A supervisor that is not this process, and is alive.
    monkeypatch.setattr(op, "_running_loop_pid", lambda instance: 999_999)

    with pytest.raises(SystemExit) as exc:
        op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                         adopt=True)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "already running" in err and "999999" in err, (
        f"the refusal must name the competing supervisor: {err!r}")


def test_this_process_is_not_mistaken_for_a_competing_supervisor(adoptable,
                                                                monkeypatch):
    """The guard compares against `os.getpid()`, and must not refuse itself.

    This supervisor overwrites the startup record with its own pid as its first
    act, so a check that ignored whose pid it was would make every adoption
    refuse -- and `restart-loop` would never work at all.
    """
    import os

    inst = op.Instance("self")
    _live(adoptable, inst)
    _own(inst)
    monkeypatch.setattr(op, "_running_loop_pid", lambda instance: os.getpid())

    def stop_at_once(instance):
        instance.stop_marker.touch()
        return False

    monkeypatch.setattr(op, "is_copilot_running", stop_at_once)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    monkeypatch.setattr(op, "start_session", _must_not_launch)

    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                          adopt=True)
    assert rc == 0, "a supervisor refused to adopt on the strength of its own pid"


# ── what adoption does, once it is allowed ───────────────────────


def _must_not_launch(instance, args, session_num, remain_on_exit=False,
                     preamble=""):
    raise AssertionError(
        "adoption launched a session; the whole point is that the running one "
        "is taken over rather than replaced")


def test_adoption_launches_nothing(adoptable, monkeypatch):
    """No launch, no preamble, no resume: the session is already up.

    Counted rather than left to `_must_not_launch`, so the proof is visible in
    the test body. A raise inside a helper is invisible to a reader scanning
    for what this asserts -- and to the suite's own check that every test
    produces a verdict, which caught this file.
    """
    inst = op.Instance("taken-over")
    _live(adoptable, inst)
    _own(inst)
    launches = {"n": 0}

    def count(instance, args, session_num, remain_on_exit=False, preamble=""):
        launches["n"] += 1

    monkeypatch.setattr(op, "start_session", count)
    monkeypatch.setattr(op, "is_copilot_running", lambda instance: True)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    inst.detach_marker.touch()
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False, adopt=True)

    assert launches["n"] == 0, (
        "adoption launched a session; the whole point is that the running one "
        "is taken over rather than replaced")


def test_the_adopted_session_is_left_running(adoptable, monkeypatch):
    """Continuity is the feature. A supervisor swap must not cost the session.

    The detach marker is set with copilot still *running*, which is the shape
    `operator stop-loop` produces: the supervisor notices during its poll and
    stands down. Reporting copilot as gone instead would be a crash, and the
    loop would correctly relaunch -- a different path, and not this one.
    """
    inst = op.Instance("survives")
    _live(adoptable, inst)
    _own(inst)
    monkeypatch.setattr(op, "start_session", _must_not_launch)
    monkeypatch.setattr(op, "is_copilot_running", lambda instance: True)
    killed = {"n": 0}
    monkeypatch.setattr(op, "stop_session_gracefully",
                        lambda instance: killed.__setitem__("n", killed["n"] + 1))

    inst.detach_marker.touch()
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False,
                          adopt=True)

    assert rc == 0
    assert killed["n"] == 0, "a detached supervisor stopped the session it adopted"
    assert inst.session in adoptable.sessions, (
        "the adopted session was destroyed by the supervisor that took it over")


def test_an_adopted_session_keeps_its_own_number(adoptable, monkeypatch):
    """Adoption joins the session already running; only a launch moves on.

    Numbering it as the *next* session would make the ledger show a session
    that never existed, and the one actually running would never be recorded
    as ending.
    """
    inst = op.Instance("numbered")
    inst.save_state(7, "2026-01-01T00:00:00Z")
    _live(adoptable, inst)
    _own(inst)
    monkeypatch.setattr(op, "start_session", _must_not_launch)
    monkeypatch.setattr(op, "is_copilot_running", lambda instance: True)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    inst.detach_marker.touch()
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False, adopt=True)

    log = op.LOG_FILE.read_text(encoding="utf-8")
    assert "#7" in log, (
        f"the adopted session was not numbered 7; the log says:\n{log[-600:]}")
    assert "#8" not in log, "adoption consumed a session number for no launch"


def test_the_progress_breaker_rearms_after_an_adopted_session(adoptable,
                                                              monkeypatch):
    """This supervisor never saw the state that session began with.

    Measuring against a baseline taken part-way through would compare the
    repository to itself and report a session that had been working for an hour
    as having changed nothing.
    """
    inst = op.Instance("rearms")
    _live(adoptable, inst)
    _own(inst)
    monkeypatch.setattr(op, "start_session", _must_not_launch)
    monkeypatch.setattr(op, "workspace_fingerprint", lambda cwd: "unmoving")

    def stop_at_once(instance):
        instance.stop_marker.touch()
        return False

    monkeypatch.setattr(op, "is_copilot_running", stop_at_once)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False, adopt=True)

    log = op.LOG_FILE.read_text(encoding="utf-8")
    assert "re-arms after the adopted session" in log, (
        f"the breaker measured an adopted session against a baseline it never "
        f"saw; the log says:\n{log[-600:]}")
