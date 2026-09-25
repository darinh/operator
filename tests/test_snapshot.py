"""The board `operator list` prints, and the promise the preamble makes about it.

A stale supervisor is the one state where an agent is being actively
misinformed, so the preamble tells it, in the only CAUTION it ever prints,
that ``operator list`` "names the changed files and every instance affected".
That sentence shipped against a command which printed the seat name and
nothing else. Measured on this repository on 2026-09-25, against a live
supervisor that really was stale::

    $ operator list
      x
    $ python -c "... loop_code_state(Instance('x'), 23152)"
    ('stale', ['...\\exits.py', '...\\instance.py', '...\\paths.py',
               '...\\preamble.py'])

The kernel had the whole answer and the command threw it away:
`loop_record_facts` returns ``changed`` beside ``code``, `instance_snapshot`
dropped it, and no board existed to print either. So the agent that went and
checked, because the preamble told it to, learned less than the agent that
did not.

`test_preamble_runnable.py` did not catch it and could not have. It grades the
*program* a clause names -- ``operator`` is installed, so the clause passed --
and nothing there reads what the sentence promises the program will say. That
is the gap this file covers: the clause and the command are asserted against
each other, so drifting either one fails.
"""
from __future__ import annotations

import json

import op
import pytest

from operator_cli import entry

PID = 4242


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "TABS_FILE", tmp_path / "tabs.json")
    return tmp_path


@pytest.fixture
def looping(monkeypatch):
    """One managed seat with a live session and a live supervisor."""
    inst = op.Instance("alpha")
    inst.claim("tok")
    monkeypatch.setattr(op.MUX, "available", lambda: True)
    monkeypatch.setattr(op.MUX, "list_sessions", lambda: [inst.id])
    monkeypatch.setattr(op, "_running_loop_pid", lambda i: PID)
    monkeypatch.setattr(op, "_running_loop_identity", lambda i: (PID, None))
    return inst


def _record(instance, *paths) -> None:
    """A startup record claiming digests that no longer match ``paths``."""
    instance.loop_code_file.write_text(
        json.dumps({"pid": PID, "files": [{"path": str(p), "sha256": "0" * 64}
                                          for p in paths]}),
        encoding="utf-8")


def _changed_file(tmp_path, name="preamble.py"):
    moved = tmp_path / name
    moved.write_text("this is not what the supervisor imported", encoding="utf-8")
    return moved


def test_the_board_names_the_file_that_changed(looping, tmp_path, capsys):
    """The live defect, driven through the command the preamble names.

    Not `list_instances` directly: the preamble advertises `operator list`,
    and the verb dispatch is half of what shipped broken.
    """
    moved = _changed_file(tmp_path)
    _record(looping, moved)

    assert entry.main(["list"]) == 0

    out = capsys.readouterr().out
    assert looping.display_name in out, out
    assert str(moved) in out, (
        f"the preamble promises `operator list` names the changed files, and "
        f"{moved} changed under this supervisor. The board printed:\n{out}")


def test_the_stale_clause_and_the_board_are_asserted_against_each_other(
        looping, tmp_path, capsys):
    """Either side drifting fails here, which is the point of reading both.

    A guard that hard-codes the promise keeps passing after somebody rewrites
    the sentence to promise something else, and a guard that only reads the
    sentence never notices the command going quiet.
    """
    import preamble as P

    clause = P.build_preamble("a:b", op.Instance("alpha"),
                              code_state=P.CODE_STALE)
    assert "`operator list`" in clause, (
        "the stale CAUTION no longer sends the agent to `operator list`, so "
        "the promise this file grades has moved and the test must follow it")
    assert "changed files" in clause, clause

    moved = _changed_file(tmp_path)
    _record(looping, moved)
    assert entry.main(["list"]) == 0
    assert str(moved) in capsys.readouterr().out


def _board(monkeypatch, capsys, verdict, changed=()):
    snap = {"name": "alpha", "id": op.Instance("alpha").id, "loop_pid": PID,
            "loop_code": verdict, "loop_changed": list(changed)}
    monkeypatch.setattr(op, "active_instances", lambda: [op.Instance("alpha")])
    monkeypatch.setattr(op, "instance_snapshot", lambda inst: snap)
    capsys.readouterr()
    assert op.list_instances() == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("verdict", [op.CODE_STALE, op.CODE_UNRECORDED,
                                     op.CODE_MISMATCH, op.CODE_UNKNOWN])
def test_a_row_that_is_not_current_never_reads_like_a_healthy_one(
        verdict, monkeypatch, capsys):
    """Backlog 0001's failure shape, asserted as a difference rather than a phrase.

    Pinning the wording would pass on a board that prints the same reassuring
    row for every verdict as long as one substring survived somewhere. What
    actually matters is that a reader can tell the two apart, so the healthy
    output is the control and inequality is the assertion.

    ``unknown`` is in here deliberately. It is exempt from a *remedy* -- a
    restart cannot fix "nobody could look" -- and it was exempt from saying
    anything at all, which is how the instrument came to be byte-identical on
    a machine where six of six supervisors could not be described.
    """
    healthy = _board(monkeypatch, capsys, op.CODE_CURRENT)
    assert healthy.strip(), "the healthy control printed nothing at all"
    assert _board(monkeypatch, capsys, verdict) != healthy, (
        f"a {verdict} supervisor is reported byte-for-byte as a healthy one")


def test_a_current_supervisor_gets_no_caveat(monkeypatch, capsys):
    """The common case stays quiet, for the reason `_code_state_notice` gives:
    a caveat attached to every row is one that stops being read."""
    out = _board(monkeypatch, capsys, op.CODE_CURRENT)
    assert "restart-loop" not in out, out


@pytest.mark.parametrize("verdict", [op.CODE_STALE, op.CODE_UNRECORDED,
                                     op.CODE_MISMATCH])
def test_the_remedy_offers_the_sweep_not_a_command_per_instance(
        verdict, monkeypatch, capsys):
    """Carried over from `tests/pending/test_restart_all_loops.py`.

    An operator change makes every supervisor stale at the same instant, so
    the sweep is the normal case. `operator list` once named eight stale
    supervisors and printed eight commands to type, and a remedy applied by
    hand once per instance is a remedy applied to some of them.

    The pending file itself stays where it is: it also wants `op.METRICS_DB`
    and a kernel-namespace `main`, and the kernel is defined as having
    neither. The incident is what transfers, not the fixture.
    """
    names = ("alpha", "beta", "gamma")
    snaps = {n: {"name": n, "id": op.Instance(n).id, "loop_pid": PID,
                 "loop_code": verdict, "loop_changed": []} for n in names}
    monkeypatch.setattr(op, "active_instances",
                        lambda: [op.Instance(n) for n in names])
    monkeypatch.setattr(op, "instance_snapshot",
                        lambda inst: snaps[inst.display_name])

    assert op.list_instances() == 0
    out = capsys.readouterr().out

    assert "operator restart-loop --all" in out, (
        f"the {verdict} notice does not offer the sweep:\n{out}")
    offered = [f"operator restart-loop {n}" for n in names
               if f"operator restart-loop {n}" in out]
    assert offered == [], (
        f"the {verdict} notice still prints a command per instance: {offered}. "
        f"Three supervisors go stale together; three commands to type is how "
        f"one of them gets missed.")


def test_every_verdict_is_either_reported_or_exempt_with_a_reason(capsys,
                                                                  monkeypatch):
    """A newly added verdict must not be a verdict the board is silent about.

    The hand-written parametrisations above are lists a new constant is simply
    absent from, which is the shape of a guard that keeps passing over a set
    that no longer describes the code.
    """
    declared = {v for n, v in vars(op).items()
                if n.startswith("CODE_") and isinstance(v, str)}
    healthy = _board(monkeypatch, capsys, op.CODE_CURRENT)
    silent = [v for v in sorted(declared - {op.CODE_CURRENT})
              if _board(monkeypatch, capsys, v) == healthy]
    assert silent == [], (
        f"these verdicts are reported as a healthy supervisor: {silent}")


def test_the_snapshot_carries_the_files_that_changed(looping, tmp_path):
    """`loop_record_facts` returns ``changed`` and the snapshot dropped it.

    The board cannot name a file the row never carried, so this is the layer
    the defect actually lived at -- and a board test alone would let a future
    snapshot drop it again while a stub kept the printing green.
    """
    moved = _changed_file(tmp_path)
    _record(looping, moved)

    snap = op.instance_snapshot(looping)

    assert snap["loop_code"] == op.CODE_STALE
    assert snap["loop_changed"] == [str(moved)]


def test_a_supervisor_that_is_not_running_is_not_described(monkeypatch, capsys):
    """Every notice is gated on a live loop pid, and that gate is load-bearing.

    A seat whose supervisor was stopped has no imported code to be behind
    disk, so a staleness verdict about it is a verdict about nothing. The
    `_running_loop_pid` recycled-pid work exists because this gate switches
    four notices on and off at once.
    """
    snap = {"name": "alpha", "id": op.Instance("alpha").id, "loop_pid": None,
            "loop_code": op.CODE_STALE, "loop_changed": ["whatever.py"]}
    monkeypatch.setattr(op, "active_instances", lambda: [op.Instance("alpha")])
    monkeypatch.setattr(op, "instance_snapshot", lambda inst: snap)

    assert op.list_instances() == 0
    out = capsys.readouterr().out

    assert "alpha" in out, "the seat is running and must still be listed"
    assert "whatever.py" not in out, out
    assert "restart-loop" not in out, out


def test_no_running_seats_is_not_a_failure(monkeypatch, capsys):
    monkeypatch.setattr(op, "active_instances", lambda: [])
    assert op.list_instances() == 0
    assert capsys.readouterr().out.strip() == "No running seats."
