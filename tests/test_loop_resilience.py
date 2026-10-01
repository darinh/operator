"""Loop-mode resilience: a launch failure must not kill an unattended loop."""
from __future__ import annotations

from pathlib import Path

import pytest

import op
from mux import MuxSessionError


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "COPILOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    monkeypatch.setattr(op, "LAUNCH_BACKOFF_BASE", 0)
    monkeypatch.setattr(op, "RESTART_PAUSE_SECONDS", 0)
    workdir = tmp_path / "not-a-repo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


def test_launch_failure_retries_then_succeeds(monkeypatch):
    """A transient backend failure must be retried, not fatal."""
    attempts = {"n": 0}

    def flaky(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise MuxSessionError("simulated silent failure")
        instance.exit_file.write_text("0", encoding="utf-8")
        # A launch that succeeds and then exits with no restart marker is now
        # treated as an unexpected crash and relaunched; stop the loop here so
        # this test only exercises the launch-failure retry, not that.
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", flaky)

    inst = op.Instance("retry")
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert rc == 0
    assert attempts["n"] == 2, "launch should have been retried after failure"


def test_persistent_launch_failure_eventually_gives_up(monkeypatch):
    """Retrying forever would spin silently; there must be a bound."""
    attempts = {"n": 0}

    def always_fail(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        raise MuxSessionError("always fails")

    monkeypatch.setattr(op, "start_session", always_fail)

    inst = op.Instance("giveup")
    with pytest.raises(MuxSessionError):
        op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)
    assert attempts["n"] == op.MAX_LAUNCH_FAILURES


def test_resume_id_is_restored_when_launch_fails(monkeypatch):
    """A failed launch must not consume the saved resume id."""
    seen = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(list(args))
        if len(seen) == 1:
            raise MuxSessionError("boom")
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", capture)

    inst = op.Instance("resume")
    sid = "3f2a9c1e-1111-2222-3333-444455556666"
    inst.save_state(2, "2026-07-27T10:00:00Z", sid)

    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert any(f"--resume={sid}" in a for a in seen[0])
    assert any(f"--resume={sid}" in a for a in seen[1]), \
        "resume id must survive a failed launch"


def test_resume_without_handoff_file_gets_crash_note(monkeypatch, tmp_path):
    """Resuming with no handoff file for the project is treated as crash
    recovery and gets a note added to the preamble."""
    seen_preambles = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen_preambles.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", capture)
    monkeypatch.setattr(op, "project_handoff_file", lambda cwd, instance_id="": None)

    inst = op.Instance("crashy")
    sid = "3f2a9c1e-1111-2222-3333-444455556666"
    inst.save_state(1, "2026-07-27T10:00:00Z", sid)

    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert "crash" in seen_preambles[0].lower()


def test_resume_with_handoff_file_present_has_no_crash_note(monkeypatch, tmp_path):
    """When the project's handoff file exists, resuming is a normal
    continuation, not crash recovery — no note should be added."""
    seen_preambles = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen_preambles.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    handoff = tmp_path / "next-session.md"
    handoff.write_text("# handoff", encoding="utf-8")

    monkeypatch.setattr(op, "start_session", capture)
    monkeypatch.setattr(op, "project_handoff_file", lambda cwd, instance_id="": handoff)

    inst = op.Instance("tidy")
    sid = "3f2a9c1e-1111-2222-3333-444455556666"
    inst.save_state(1, "2026-07-27T10:00:00Z", sid)

    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert "crash" not in seen_preambles[0].lower()


def test_fresh_run_has_no_crash_note(monkeypatch):
    """A --fresh loop has no resume id at all, so there is nothing to be a
    crash recovery of."""
    seen_preambles = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen_preambles.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", capture)

    inst = op.Instance("fresh-run")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert "crash" not in seen_preambles[0].lower()


# ── the crash note is a claim about one moment ──────────────────
#
# It used to be decided once, before the supervisor's loop started, and baked
# into a preamble every later session reused. Every test above reads
# `seen_preambles[0]`, so all of them passed throughout: a verdict that is
# only ever checked on the first launch cannot be caught going stale on the
# second. `copilot-tools` reached session #223 on a run started 25 days
# earlier, still reporting the answer taken at loop start.


def _loop_with_handoff(monkeypatch, handoff: Path, script):
    """Run a loop whose sessions are driven by ``script(n, instance)``."""
    seen: list[str] = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(preamble)
        script(len(seen), instance)

    monkeypatch.setattr(op, "start_session", capture)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    monkeypatch.setattr(op, "project_handoff_file", lambda cwd, instance_id="": handoff)
    return seen


def test_a_handoff_written_by_session_one_silences_the_note_for_session_two(
        monkeypatch, tmp_path):
    """The false positive, observed live on 2026-08-05.

    A session wrote its handoff, the supervisor relaunched off the restart
    marker 33 seconds later, and the new session was told no handoff could be
    found -- while the file sat on disk, unread, being simultaneously offered
    to it by point (3) of the same preamble.
    """
    handoff = tmp_path / "next-session.md"  # absent when the loop starts

    def script(n, instance):
        if n == 1:
            # Exactly what `handoff` does: write the file, ask for a restart.
            handoff.write_text("# handoff", encoding="utf-8")
            instance.restart_marker.touch()
        else:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, handoff, script)

    inst = op.Instance("re-decided")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert len(seen) >= 2, "the loop must have launched a second session"
    assert "crash" in seen[0].lower(), (
        "no handoff existed when the loop started, so the first session is "
        "the control: without it a note that never appears would pass too")
    assert "crash" not in seen[1].lower()


def test_a_handoff_at_loop_start_does_not_silence_a_later_crash(
        monkeypatch, tmp_path):
    """The false negative, and the more expensive direction.

    A loop that started while a handoff happened to be sitting there never
    reported crash recovery again -- so the mid-turn kills this note exists to
    surface were silent for the entire run.
    """
    handoff = tmp_path / "next-session.md"
    handoff.write_text("# handoff", encoding="utf-8")

    def script(n, instance):
        if n == 1:
            # The protocol: read the handoff, then delete it. Then die with no
            # marker to explain it, which is the shape of an external kill.
            handoff.unlink()
        else:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, handoff, script)

    inst = op.Instance("stale-clean")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert len(seen) >= 2
    assert "crash" not in seen[0].lower()
    assert "crash" in seen[1].lower(), (
        "the handoff was consumed and the session then died unexplained; "
        "that is exactly what this note is for")


def test_a_fresh_run_still_reports_a_crash_after_its_first_session(
        monkeypatch, tmp_path):
    """`--fresh` has no predecessor to judge, and that was read as "never".

    The old verdict was gated on a resume id, which a fresh run never has, so
    a `--fresh` loop could not produce this note at any point in its life --
    no matter how many of its own sessions were later killed mid-turn.
    """
    handoff = tmp_path / "next-session.md"  # never written by anyone

    def script(n, instance):
        if n > 1:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, handoff, script)

    inst = op.Instance("fresh-then-killed")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert len(seen) >= 2
    assert "crash" not in seen[0].lower(), (
        "nothing preceded the first session of a fresh run, so there is no "
        "predecessor to accuse")
    assert "crash" in seen[1].lower()


def test_an_unregistered_project_is_never_reported_as_a_crash(
        monkeypatch, tmp_path):
    """The absence of a handoff proves nothing where one could never be
    written. This holds per launch too, not just on the first."""

    def script(n, instance):
        if n > 1:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, None, script)

    inst = op.Instance("unregistered")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert len(seen) >= 2
    assert not any("crash" in p.lower() for p in seen)


def test_an_unreadable_catalog_is_never_reported_as_a_crash(
        monkeypatch, tmp_path):
    """A probe that failed has established nothing about the last session."""

    def script(n, instance):
        if n > 1:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, op.CATALOG_UNREADABLE, script)

    inst = op.Instance("unreadable-catalog")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert len(seen) >= 2
    assert not any("crash" in p.lower() for p in seen)


def test_an_unexaminable_handoff_is_never_reported_as_a_crash(
        monkeypatch, tmp_path):
    """`path_present` is tri-state on purpose: a denied probe is not an
    absent file, and only absence is evidence about the last session."""
    handoff = tmp_path / "next-session.md"

    def script(n, instance):
        if n > 1:
            instance.stop_marker.touch()
        instance.exit_file.write_text("0", encoding="utf-8")

    seen = _loop_with_handoff(monkeypatch, handoff, script)

    # Denied for the handoff only. Blanketing `path_present` would also blind
    # the stop-marker reads, and the loop would then end by exhausting
    # its unreadable-marker budget after a single session -- with `seen`
    # holding one entry and no note in it, which is what this asserts anyway.
    real_present = op.path_present
    monkeypatch.setattr(
        op, "path_present",
        lambda p: None if Path(p) == handoff else real_present(p))

    inst = op.Instance("unexaminable")
    inst.save_state(1, "2026-07-27T10:00:00Z",
                    "3f2a9c1e-1111-2222-3333-444455556666")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert len(seen) >= 2, (
        "the loop must reach a second launch, or the tri-state handling is "
        "not what ended it")
    assert not any("crash" in p.lower() for p in seen)


# ── a session that ended by handoff must be recorded as one ─────








def test_a_continued_run_without_a_resume_id_still_has_a_predecessor(
        monkeypatch, tmp_path):
    """A missing resume id does not mean there was no previous session.

    The verdict is gated on there having been a predecessor to ask about, and
    that was read off `resume_id` -- which is only written when the previous
    session reported an id that parses as a UUID. A run five sessions deep
    whose id went missing would be treated as a first launch, and the crash
    note it exists to give would be skipped exactly once, on the relaunch most
    likely to be following something that went wrong.
    """
    handoff = tmp_path / "next-session.md"  # absent: the predecessor left none

    def script(n, instance):
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    seen = _loop_with_handoff(monkeypatch, handoff, script)

    inst = op.Instance("no-resume-id")
    inst.save_state(5, "2026-07-27T10:00:00Z")  # no session id recorded
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=False)

    assert seen, "the loop must have launched"
    assert "crash" in seen[0].lower(), (
        "session #6 had a predecessor; `test_fresh_run_has_no_crash_note` is "
        "the control that this note is not simply always present")




def test_unexpected_exit_without_marker_is_relaunched(monkeypatch):
    """An unexpected session death with no restart marker and no stop marker
    must be relaunched automatically rather than ending the loop."""
    attempts = {"n": 0}

    def flaky_session(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        if attempts["n"] < 3:
            instance.exit_file.write_text("0", encoding="utf-8")
        else:
            instance.exit_file.write_text("0", encoding="utf-8")
            instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", flaky_session)

    inst = op.Instance("relaunch-me")
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert rc == 0
    assert attempts["n"] == 3, "each unexpected exit should trigger a fresh launch"


def test_repeated_unexpected_exits_eventually_give_up(monkeypatch):
    """Unbounded crash-relaunching would spin forever; there must be a cap
    distinct from (but the same size as) the launch-failure cap."""
    attempts = {"n": 0}

    def always_crashes(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        instance.exit_file.write_text("0", encoding="utf-8")

    monkeypatch.setattr(op, "start_session", always_crashes)

    inst = op.Instance("doomed")
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert rc == 1
    assert attempts["n"] == op.MAX_LAUNCH_FAILURES


def test_a_session_that_ran_for_minutes_does_not_count_toward_the_give_up_limit(
        monkeypatch):
    """Five deaths hours apart must not retire a loop the way five in a minute do.

    The consecutive-exit limit is there to stop a hot relaunch spin, but it
    counted exits and never their spacing. This machine's operator.log shows
    what that costs: on four separate occasions every instance died within
    seconds of every other, independent of when each was launched, each having
    run for minutes. Five such waves and every supervisor retired itself, so
    the user came back to nothing running.

    A session that stayed up past the healthy threshold restarts the count, so
    only genuinely rapid failures can still exhaust it. The negative control is
    `test_repeated_unexpected_exits_eventually_give_up`, whose sessions die
    instantly and must still give up at the cap.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    keep_going = op.MAX_LAUNCH_FAILURES * 3

    def dies(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        if attempts["n"] > keep_going:
            # Unbounded by construction now, so the test must end it.
            raise KeyboardInterrupt
        instance.exit_file.write_text("0", encoding="utf-8")

    really_running = op.is_copilot_running

    def aged(instance):
        # Age the session past the healthy threshold before its death is
        # noticed, which is the only way the supervisor can tell a session
        # that ran from one that never started.
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", dies)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    inst = op.Instance("long-lived")
    op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert attempts["n"] > op.MAX_LAUNCH_FAILURES, (
        "the supervisor gave up at the cap even though every session had been "
        "up for longer than HEALTHY_SESSION_SECONDS before it died")
    assert attempts["n"] == keep_going + 1


def test_stop_marker_stops_session_and_supervisor(monkeypatch):
    """`operator stop NAME` (a touched stop marker) must stop both the
    supervisor and the session."""
    calls = {"stop_gracefully": 0, "kill_session": 0}
    session_live = {"v": False}

    def start(instance, args, session_num, remain_on_exit=False, preamble=""):
        instance.session_file.write_text(
            "11111111-2222-3333-4444-555555555555", encoding="utf-8")
        instance.stop_marker.touch()
        session_live["v"] = True

    def fake_stop_gracefully(instance):
        calls["stop_gracefully"] += 1

    monkeypatch.setattr(op, "start_session", start)
    monkeypatch.setattr(op, "stop_session_gracefully", fake_stop_gracefully)
    monkeypatch.setattr(op.MUX, "has_session", lambda session: session_live["v"])
    monkeypatch.setattr(op.MUX, "kill_session", lambda session: calls.__setitem__(
        "kill_session", calls["kill_session"] + 1) or True)
    monkeypatch.setattr(op.MUX, "pane_dead", lambda session: False)

    inst = op.Instance("stop-me")
    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert rc == 0
    assert calls["stop_gracefully"] == 1
    assert calls["kill_session"] == 1
    assert not inst.stop_marker.exists()
    assert not inst.loop_pid_file.exists()
