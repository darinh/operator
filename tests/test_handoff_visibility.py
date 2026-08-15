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

So the remedy is the one used for the mandate: state the observed thing, name
the address, and write down that it was said. Every prohibition below has a
control asserting it fires, because a guard that cannot fire reads exactly
like coverage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator_kernel"))

import op  # noqa: E402


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    return op.Instance(display_name="copilot-tools")


def _preamble(seat, **kwargs):
    return op.build_preamble("anvil:anvil", seat, **kwargs)


# --- the incident itself ----------------------------------------------------

def test_a_waiting_handoff_is_named_in_the_preamble(seat):
    """The assertion that would have prevented the incident.

    Not "the preamble mentions handoffs" -- it always did, in the standing
    instruction -- but that it carries *this* handoff's address.
    """
    where = r"C:\Users\darin\.operator\projects\c48add2d\handoff\copilot-tools.md"
    text = _preamble(seat, handoff_waiting=where)
    assert where in text, (
        "the session was not told where its handoff is, which is the whole "
        "defect: it has to go looking, and an agent that does not look is "
        "indistinguishable from one with nothing to read")


def test_a_granting_path_cannot_kill_the_supervisor(seat):
    """A directory name is third-party text on a code path that raises.

    `assert_no_unattributed_authority` unwinds out of `run_loop_mode`, which
    catches only `MuxError` and `KeyboardInterrupt`, so an exception here ends
    that seat's supervision permanently. The first draft interpolated the path
    straight in, and a reviewer killed a launch with a directory named
    `you have permission to`.

    This is `vet_clause`'s own reason, applied one field over from the work
    item it was written for.
    """
    hostile = r"/tmp/you have permission to/handoff.md"
    text = _preamble(seat, handoff_waiting=hostile)  # must not raise
    assert hostile not in text, "the granting path was passed through verbatim"


def test_a_refused_path_still_announces_the_handoff(seat):
    """The announcement survives a refused address.

    `vet_clause` replaces the whole body it is handed, so vetting the sentence
    and the address together would drop both -- and losing the sentence that
    says a handoff exists is the defect this file is about. Withholding the
    address costs the agent a lookup; withholding the announcement costs it
    the session.
    """
    text = _preamble(seat, handoff_waiting=r"/tmp/you have permission to/h.md")
    assert "A handoff from the previous session is waiting" in text


def test_a_refused_path_is_reported_to_the_caller(seat):
    """Withheld text has to be recorded somewhere or the refusal is silent."""
    seen = []
    _preamble(seat, handoff_waiting=r"/tmp/you are authorized to/h.md",
              on_withheld=lambda source, phrases: seen.append((source, phrases)))
    assert seen and seen[0][1] == ["you are authorized to"]


def test_an_ordinary_path_is_not_withheld(seat):
    """The control for the three above: without it they are satisfied by an
    implementation that refuses every path, which would restore the incident
    while looking like a security fix."""
    seen = []
    text = _preamble(seat, handoff_waiting="/tmp/projects/guid/handoff.md",
                     on_withheld=lambda s, p: seen.append(s))
    assert "/tmp/projects/guid/handoff.md" in text
    assert seen == []


def test_an_undetermined_handoff_is_said_out_loud(seat):
    """"Could not look" must reach the agent, not stop at the tri-state.

    Silence is read as "no handoff" -- that inference is what caused the
    incident -- so the one verdict that means *nobody knows* cannot be the one
    that produces no sentence.
    """
    text = _preamble(seat, handoff_unknown=True)
    assert "could not be determined" in text
    assert "A handoff from the previous session is waiting" not in text


def test_a_preamble_without_a_waiting_handoff_does_not_invent_one(seat):
    """The control. Without it the assertion above holds for any implementation
    that unconditionally pastes a path in."""
    text = _preamble(seat)
    assert "A handoff from the previous session is waiting" not in text
    assert "Read it before doing anything else" not in text


def test_the_waiting_clause_tells_the_agent_not_to_trust_another_tool(seat):
    """The specific wrong inference the incident turned on, refused by name.

    The agent did not ignore the instruction; it asked a *different* command
    and believed the answer. `operator session start` reports work-item
    claims, and answering "No assignment" is correct of it and says nothing
    about handoffs. A preamble that only gives an address leaves that mistake
    available, so the clause closes it explicitly.
    """
    text = _preamble(seat, handoff_waiting="/tmp/h.md")
    assert "no other command answers this question" in text


def test_a_waiting_handoff_and_crash_recovery_are_never_both_claimed(seat):
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
    text = _preamble(seat, handoff_waiting="/tmp/h.md", crash_recovery=True)
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

    `crash_recovery_verdict` answered False here, and False also meant "the
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


# --- the two answers may not drift apart ------------------------------------

def _handoff_verdicts():
    """Every HANDOFF_* constant, by introspection.

    Derived rather than listed. A hand-written sweep keeps passing over a set
    that no longer describes the code -- exactly how `CODE_MISMATCH` was added
    to its module without any parametrised test noticing -- so adding a fifth
    verdict without deciding whether it is a crash makes this fail.
    """
    return {name: value for name, value in vars(op.exits).items()
            if name.startswith("HANDOFF_") and isinstance(value, str)}


def test_every_verdict_is_covered_by_the_crash_predicate(monkeypatch):
    """`crash_recovery_verdict` must agree with `handoff_state` on all of them.

    The predicate is what tells an agent its predecessor died, and it now
    delegates. This pins the delegation for every verdict there is, so a new
    one cannot quietly default to "crash".
    """
    verdicts = _handoff_verdicts()
    assert len(verdicts) >= 4, "introspection found nothing; the sweep is inert"
    for name, verdict in verdicts.items():
        monkeypatch.setattr(op, "handoff_state",
                            lambda w, i="", v=verdict: op.HandoffState(v))
        expected = verdict == op.HANDOFF_MISSING
        assert op.crash_recovery_verdict(Path("/repo"), "seat") is expected, (
            f"{name} disagrees with the crash predicate")


# --- the record -------------------------------------------------------------

def test_the_verdict_is_recorded_so_a_skipped_handoff_is_visible(
        tmp_path, monkeypatch):
    """The half that survives the session.

    The kernel cannot make an agent read its handoff. What it can do is stop
    "was told and ignored it" from being indistinguishable from "there was
    nothing to read" -- which is what it was during the incident, in every log
    on the machine.
    """
    op.evidence.record_handoff_state(
        tmp_path, instance="copilot-tools", session=244,
        verdict=op.HANDOFF_WAITING, path=tmp_path / "copilot-tools.md")
    # A second verdict, because one is not a test of the field. Recording only
    # the waiting case is satisfied by a writer that hardcodes "waiting" --
    # mutation-verified, and that mutant survived the first draft of this test.
    op.evidence.record_handoff_state(
        tmp_path, instance="copilot-tools", session=245,
        verdict=op.HANDOFF_MISSING)

    records = [json.loads(line) for line in
               op.evidence.trace_path(tmp_path).read_text(encoding="utf-8")
               .splitlines()]
    handoffs = [r for r in records if r.get("event") == "handoff_state"]
    assert len(handoffs) == 2
    assert handoffs[0]["verdict"] == op.HANDOFF_WAITING
    assert handoffs[0]["session"] == 244
    assert handoffs[0]["path"].endswith("copilot-tools.md")
    assert handoffs[1]["verdict"] == op.HANDOFF_MISSING
    assert handoffs[1]["path"] is None, (
        "no address was established, and a placeholder path would read as one")


def test_recording_the_verdict_never_raises(tmp_path, monkeypatch):
    """Evidence is best-effort by design: a supervisor must not die because a
    record could not be written. The control is that it is *reached* -- an
    unwritable home is the failure this swallows."""
    monkeypatch.setattr(op.evidence, "_append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    op.evidence.record_handoff_state(
        tmp_path, instance="seat", session=1, verdict=op.HANDOFF_WAITING)


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
    monkeypatch.setattr(op, "show_run_summary", lambda run_started: None)
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


def test_the_loop_records_what_the_session_was_told(monkeypatch, tmp_path):
    """The record has to be written on the live path, not only in a unit test.

    `announced` is what separates "was told and ignored it" from "there was
    nothing to read" -- the two that were indistinguishable during the
    incident.
    """
    handoff = tmp_path / "copilot-tools.md"
    handoff.write_text("# Session Handoff\n", encoding="utf-8")

    _run_one_loop(monkeypatch, tmp_path, handoff)

    records = [json.loads(line) for line in
               op.evidence.trace_path(tmp_path / "home")
               .read_text(encoding="utf-8").splitlines()]
    handoffs = [r for r in records if r.get("event") == "handoff_state"]
    assert handoffs, "the live path wrote no handoff record at all"
    assert handoffs[0]["verdict"] == op.HANDOFF_WAITING
    assert handoffs[0]["announced"] is True
    assert handoffs[0]["path"] == str(handoff)


def test_the_loop_tells_a_session_when_nobody_could_look(monkeypatch, tmp_path):
    """The verdict that means *nobody knows* has to survive the wiring too.

    It is the one most easily lost: `HANDOFF_UNKNOWN` produces no crash note
    and no address, so a supervisor that simply never passed it would look
    correct in every other test here. It was, in the first draft -- this test
    is what caught the argument being dropped.
    """
    # Denied for the handoff only. Blanketing `path_present` also blinds the
    # stop/detach marker probes, and the loop then spends its whole
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


def test_the_loop_still_reports_a_missing_handoff_as_crash_recovery(
        monkeypatch, tmp_path):
    """The control for the two above, and a regression guard on the behaviour
    that already existed: without it they are satisfied by an implementation
    that announces a handoff unconditionally."""
    absent = tmp_path / "nothing-here.md"

    preamble = _run_one_loop(monkeypatch, tmp_path, absent)

    assert "A handoff from the previous session is waiting" not in preamble
    assert "could not be found" in preamble

    # And the record says nothing was announced. Without this the `announced`
    # field is satisfied by a writer that hardcodes True, which would make the
    # one thing it exists to distinguish -- told versus not told -- unreadable
    # again.
    records = [json.loads(line) for line in
               op.evidence.trace_path(tmp_path / "home")
               .read_text(encoding="utf-8").splitlines()]
    handoffs = [r for r in records if r.get("event") == "handoff_state"]
    assert handoffs[0]["verdict"] == op.HANDOFF_MISSING
    assert handoffs[0]["announced"] is False
