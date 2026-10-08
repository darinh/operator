"""A session must be told when a handoff is waiting for it.

The incident, 2026-08-15. A supervised session was launched 6 seconds after its
predecessor wrote a full handoff. The launch preamble said nothing about it,
because the only branch that produced a clause was the one for a handoff being
*absent*. The agent ran `operator session start`, got "No assignment" -- an
answer about work-item claims, not about handoffs -- concluded there was no
handoff, and spent its session inventing work in a repository that had been
frozen three days earlier.

The standing instruction "always check for a session handoff file" was in the
preamble the whole time. It is not enough, and the reason is this repository's
north star: **a session that skipped the handoff produced a transcript
identical to one that had nothing to read.** Nothing on the machine recorded
which of those two had happened.

Every prohibition below has a
control asserting it fires, because a guard that cannot fire reads exactly
like coverage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator_kernel"))

import op  # noqa: E402


@pytest.fixture
def operator(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    return op.Instance("copilot-tools")


def _preamble(operator, **kwargs):
    return op.build_preamble(operator, **kwargs)


# --- the incident itself ----------------------------------------------------

def test_a_waiting_handoff_is_named_in_the_preamble(operator):
    """The assertion that would have prevented the incident.

    Not "the preamble mentions handoffs" -- it always did, in the standing
    instruction -- but that it carries *this* handoff's address.
    """
    where = r"C:\Users\darin\.operator\projects\c48add2d\handoff\copilot-tools.md"
    text = _preamble(operator, handoff_waiting=where)
    assert where in text, (
        "the session was not told where its handoff is, which is the whole "
        "defect: it has to go looking, and an agent that does not look is "
        "indistinguishable from one with nothing to read")


def test_a_refused_path_still_announces_the_handoff(operator):
    """The announcement survives a refused address.

    `vet_clause` replaces the whole body it is handed, so vetting the sentence
    and the address together would drop both -- and losing the sentence that
    says a handoff exists is the defect this file is about. Withholding the
    address costs the agent a lookup; withholding the announcement costs it
    the session.
    """
    text = _preamble(operator, handoff_waiting=r"/tmp/you have permission to/h.md")
    assert "A handoff from the previous session is waiting" in text


# --- staleness, which the kernel reports rather than judges -----------------

def test_the_age_of_the_handoff_is_reported(operator):
    """Nothing deletes a handoff except its reader, and that is a convention.

    So a file on disk is either one nobody picked up or one a session read and
    died before removing, and those want opposite responses. The supervisor
    cannot tell them apart -- it can only say when the file was written and let
    the session compare that against its own clock.
    """
    text = _preamble(operator, handoff_waiting="/tmp/h.md",
                     handoff_written="2026-08-15T21:06:21Z")
    assert "2026-08-15T21:06:21Z" in text


def test_the_delete_step_is_stated(operator):
    """The cheapest fix for the cause rather than the symptom.

    A reader that deletes leaves nothing stale to re-announce, and the reason
    agents skip it is that nothing ever told them it was theirs to do.
    """
    text = _preamble(operator, handoff_waiting="/tmp/h.md",
                     handoff_written="2026-08-15T21:06:21Z")
    assert "then delete it" in text


def test_an_unreadable_timestamp_still_announces_the_handoff(operator):
    """A stat that failed must not cost the announcement.

    The control for the two above: without it they are satisfied by an
    implementation that only announces a handoff when it can date one, which
    would drop the announcement in precisely the conditions -- a denied or
    racing filesystem -- where a session most needs it.
    """
    text = _preamble(operator, handoff_waiting="/tmp/h.md", handoff_written="")
    assert "A handoff from the previous session is waiting" in text
    assert "/tmp/h.md" in text
    assert "(UTC)" not in text


def test_a_failed_stat_yields_no_timestamp_rather_than_raising(
        tmp_path, monkeypatch):
    """`_written_at` is on the launch path, so it may not raise.

    The test above proves the *composer* copes with an empty timestamp; this
    proves the thing that produces one actually returns empty instead of
    unwinding into `run_loop_mode`, which catches neither `OSError` nor
    anything else it would arrive as.
    """
    handoff = tmp_path / "copilot-tools.md"
    handoff.write_text("# Session Handoff\n", encoding="utf-8")

    real_stat = Path.stat
    calls = []

    def denied(self, *args, **kwargs):
        if self == handoff:
            calls.append(self)
            raise OSError("permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    assert op._written_at(handoff) == ""
    assert calls, "the denial was never reached, so this asserts nothing"


def test_a_stale_handoff_is_never_suppressed(tmp_path, monkeypatch):
    """The reviewer's proposed fix, refused deliberately and on the record.

    Treating a handoff older than the previous session as absent was proposed
    to stop stale ones being announced. It is refused: "the agent did not
    delete it" is not evidence that the agent read it, so the rule would
    silently drop a session's accumulated context -- the exact harm this file
    exists to prevent, and a far worse trade than one unnecessary read.

    An ancient handoff is still a waiting handoff.
    """
    import os

    ancient = tmp_path / "copilot-tools.md"
    ancient.write_text("# Session Handoff\n", encoding="utf-8")
    long_ago = 1_600_000_000  # 2020-09-13
    os.utime(ancient, (long_ago, long_ago))

    state = _classify(monkeypatch, ancient)
    assert state.verdict == op.HANDOFF_WAITING
    assert state.written.startswith("2020-09-13")


def test_an_undetermined_handoff_is_said_out_loud(operator):
    """"Could not look" must reach the agent, not stop at the tri-state.

    Silence is read as "no handoff" -- that inference is what caused the
    incident -- so the one verdict that means *nobody knows* cannot be the one
    that produces no sentence.
    """
    text = _preamble(operator, handoff_unknown=True)
    assert "could not be determined" in text
    assert "A handoff from the previous session is waiting" not in text


def test_a_preamble_without_a_waiting_handoff_does_not_invent_one(operator):
    """The control. Without it the assertion above holds for any implementation
    that unconditionally pastes a path in."""
    text = _preamble(operator)
    assert "A handoff from the previous session is waiting" not in text
    assert "Read it before doing anything else" not in text


def test_a_waiting_handoff_and_crash_recovery_are_never_both_claimed(operator):
    """They are contradictory sentences and must not both reach a session.

    Both derive from one probe in `supervisor.py`, so today they cannot
    disagree -- but `build_preamble` takes them as two independent arguments,
    and the first draft of this file emitted both when handed both. A caller
    that probes twice is one edit away, and the session would then be told its
    predecessor crashed *and* where its predecessor's handoff is.

    Made unrepresentable in the output rather than asserted at the call site:
    a waiting handoff is a positive observation and the crash clause is an
    inference from absence, so the observation wins.
    """
    text = _preamble(operator, handoff_waiting="/tmp/h.md", crash_recovery=True)
    assert "A handoff from the previous session is waiting" in text
    assert "could not be found" not in text


# --- the classifier ---------------------------------------------------------

def _classify(monkeypatch, handoff_file, present=...):
    monkeypatch.setattr(op, "project_handoff_file",
                        lambda workdir, instance_id="": handoff_file)
    if present is not ...:
        monkeypatch.setattr(op, "path_present", lambda p: present)
    return op.handoff_state(Path("/repo"), "copilot-tools")


def test_a_handoff_on_disk_is_waiting_not_merely_not_a_crash(
        tmp_path, monkeypatch):
    """The state the old boolean could not express.

    The old boolean answered False here, and False also meant "the
    catalog would not open", "the probe was denied" and "this project is not
    registered". Four situations, one answer, and only this one has an
    address worth giving the agent.

    `path_present` is left real and the file is really written, so this
    exercises the probe rather than a stub of it.
    """
    handoff = tmp_path / "copilot-tools.md"
    handoff.write_text("# Session Handoff\n", encoding="utf-8")
    state = _classify(monkeypatch, handoff)
    assert state.verdict == op.HANDOFF_WAITING
    assert state.path == handoff


def test_an_absent_handoff_is_missing(tmp_path, monkeypatch):
    handoff = tmp_path / "projects" / "guid" / "handoff" / "copilot-tools.md"
    handoff.parent.mkdir(parents=True)
    state = _classify(monkeypatch, handoff)
    assert state.verdict == op.HANDOFF_MISSING


def test_a_denied_probe_is_unknown_and_never_missing(tmp_path, monkeypatch):
    """Telling an agent its predecessor crashed is a claim about the last
    session. A probe that could not look has established nothing about it.

    This is the tri-state discipline `path_present` exists for, and the one
    place where collapsing it would put a false accusation in front of every
    session on the machine.
    """
    state = _classify(monkeypatch, tmp_path / "h.md", present=None)
    assert state.verdict == op.HANDOFF_UNKNOWN
    assert state.verdict != op.HANDOFF_MISSING


def test_an_unreadable_catalog_is_unknown(monkeypatch):
    state = _classify(monkeypatch, op.CATALOG_UNREADABLE)
    assert state.verdict == op.HANDOFF_UNKNOWN


def test_an_unregistered_project_is_not_a_crash(monkeypatch):
    """No catalog entry means no handoff could ever have been written here, so
    its absence is not evidence that anything died."""
    state = _classify(monkeypatch, None)
    assert state.verdict == op.HANDOFF_UNEXPECTED
    assert state.verdict != op.HANDOFF_MISSING


# --- the record -------------------------------------------------------------


# --- the wiring, which every test above would let you delete ----------------

def _run_one_loop(monkeypatch, tmp_path, handoff_file):
    """Drive `run_loop_mode` for a single session and return its preamble.

    The unit tests above call the classifier, the composer and the recorder
    directly, so all of them stay green if `supervisor.py` stops calling any
    of them -- which restores the incident exactly while the suite reports
    success. This is the difference between asserting a call is *made* and
    asserting the behaviour *happens*, and the gap is wide enough to hold the
    whole defect.
    """
    seen = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    from conftest import FakeMux

    monkeypatch.setattr(op, "MUX", FakeMux())
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path / "home")
    monkeypatch.setattr(op, "start_session", capture)
    monkeypatch.setattr(op, "project_handoff_file",
                        lambda cwd, instance_id="": handoff_file)

    inst = op.Instance("wired")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)
    assert seen, "the loop never launched a session, so it proves nothing"
    return seen[0]


def test_the_loop_tells_a_session_about_its_waiting_handoff(
        monkeypatch, tmp_path):
    """End to end: a handoff on disk reaches the launch text.

    This is the incident reproduced as a test. Revert any one of the four
    wiring lines in `supervisor.py` and this fails; every other test in this
    file stays green.
    """
    handoff = tmp_path / "copilot-tools.md"
    handoff.write_text("# Session Handoff\n\n## Next Steps\nBuild the thing.\n",
                       encoding="utf-8")

    preamble = _run_one_loop(monkeypatch, tmp_path, handoff)

    assert "A handoff from the previous session is waiting" in preamble
    assert str(handoff) in preamble
    # And its age reaches the text. Asserted here rather than only against
    # `build_preamble`, because the argument that carries it is one more
    # wiring line that can be dropped without any unit test noticing -- which
    # is how `handoff_unknown` was found missing.
    written = op._written_at(handoff)
    assert written and written in preamble
    assert "then delete it" in preamble


def test_the_loop_tells_a_session_when_nobody_could_look(monkeypatch, tmp_path):
    """The verdict that means *nobody knows* has to survive the wiring too.

    It is the one most easily lost: `HANDOFF_UNKNOWN` produces no crash note
    and no address, so a supervisor that simply never passed it would look
    correct in every other test here. It was, in the first draft -- this test
    is what caught the argument being dropped.
    """
    # Denied for the handoff only. Blanketing `path_present` also blinds the
    # stop-marker probes, and the loop then spends its whole
    # unreadable-marker budget at the poll interval -- 50 seconds, for a test
    # that asserts one sentence. It is also a different test than the one
    # intended: the loop would be exercising its marker branch, not its
    # handoff branch.
    unreadable = tmp_path / "unreadable.md"
    real_present = op.path_present
    monkeypatch.setattr(
        op, "path_present",
        lambda p: None if Path(p) == unreadable else real_present(p))
    preamble = _run_one_loop(monkeypatch, tmp_path, unreadable)

    assert "could not be determined" in preamble
    assert "could not be found" not in preamble, (
        "a failed probe was reported as a crash, which is a claim about the "
        "previous session that nothing established")


