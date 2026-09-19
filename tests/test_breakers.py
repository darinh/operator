"""The circuit breakers that stop an unattended loop, and what they refuse to guess.

These are the only things standing between a stuck agent and a loop that
relaunches it a thousand times. Nothing else in the kernel stops a run that is
going nowhere: `MAX_SESSIONS` is 1000, which is a bound and not a safeguard, so
if these are wrong the failure is silent, expensive and discovered by a bill.

`breakers.py` had no dedicated tests before this file. It had two incidental
references across the suite, both from tests whose subject was something else,
and the loop *tripping* -- the supervisor actually returning `EXIT_NO_PROGRESS`
or `EXIT_UNACCOUNTED` -- was not exercised anywhere at all.

What is worth testing here is not the arithmetic but the refusals. Three
different situations all look like "the counter did not go up", and the module
keeps them apart on purpose:

* **unknown** -- the fingerprint could not be read. Says nothing, changes nothing.
* **unaccounted** -- the session changed nothing but nobody saw it end. Not
  charged to idleness, because a session killed from outside has usually not
  committed yet and charging it would retire the loops being killed fastest.
* **unchanged** -- the session changed nothing and ended the way the loop
  expects. This is the only one that is evidence of idleness.

The healing asymmetry between them is the subtlest thing in the file and is
pinned below: a corrupt counter is healed by a `changed` or an `unchanged`
session, and deliberately *not* by an `unaccounted` one, because there is
nothing to heal from -- inventing a streak length from a session that says
nothing about idleness would be entering a guess as an observation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import breakers
import op
from breakers import evaluate_progress, evaluate_unaccounted, workspace_fingerprint


# ── evaluate_progress: the decision table ────────────────────────


@pytest.mark.parametrize("before, after", [
    pytest.param(None, "abc", id="the before-fingerprint could not be read"),
    pytest.param("abc", None, id="the after-fingerprint could not be read"),
    pytest.param(None, None, id="neither could be read"),
])
def test_an_unmeasurable_session_leaves_the_streak_exactly_as_it_was(before, after):
    """Neither advances toward stopping nor clears what came before.

    A breaker that counted unmeasurable sessions would stop a healthy loop on a
    run of git failures; one that cleared on them would be switched off by the
    same run.
    """
    assert evaluate_progress(2, before, after, ending_accounted_for=True) == (2, "unknown")


def test_a_session_that_changed_something_clears_the_streak():
    assert evaluate_progress(2, "before", "after",
                             ending_accounted_for=True) == (0, "changed")


def test_progress_is_judged_before_the_counter_is_consulted():
    """A corrupt counter must not make demonstrable progress unreportable.

    Reporting known progress as "unknown" would leave a corrupt counter file
    corrupt forever, and a breaker that can never be re-armed has silently
    switched itself off.
    """
    assert evaluate_progress(None, "before", "after",
                             ending_accounted_for=True) == (0, "changed")


def test_an_unreadable_counter_is_healed_by_a_session_that_changed_nothing():
    """Restarting at one, because a stuck agent is exactly the case where no
    session ever writes the counter -- so a file that went corrupt would stay
    corrupt precisely when the breaker was needed.

    Restarting at one can only undercount, so the error it can make is letting
    the loop run longer, never stopping a healthy one.
    """
    assert evaluate_progress(None, "same", "same",
                             ending_accounted_for=True) == (1, "unchanged")


def test_a_session_that_changed_nothing_advances_the_streak():
    assert evaluate_progress(2, "same", "same",
                             ending_accounted_for=True) == (3, "unchanged")


def test_a_session_nobody_saw_end_is_not_charged_to_idleness():
    """The distinction the whole parameter exists for.

    Changing nothing is evidence an agent had nothing to do only if the session
    ended the way the loop expects. A session killed from outside has usually
    not committed at the point it dies, so charging it here would retire the
    loops being killed fastest.
    """
    assert evaluate_progress(2, "same", "same",
                             ending_accounted_for=False) == (2, "unaccounted")


def test_an_unaccounted_session_does_not_heal_an_unreadable_counter():
    """The asymmetry with the healing case above, and it is deliberate.

    There is nothing to heal *from*: this session says nothing about idleness,
    so inventing a streak length from it would enter a guess as an observation.
    """
    assert evaluate_progress(None, "same", "same",
                             ending_accounted_for=False) == (None, "unaccounted")


def test_the_caller_must_decide_whether_the_ending_was_accounted_for():
    """No default, because the silent version of this parameter is the bug it
    exists to fix."""
    with pytest.raises(TypeError):
        evaluate_progress(0, "same", "same")


# ── evaluate_unaccounted ─────────────────────────────────────────


def test_work_landing_clears_the_unaccounted_streak():
    """Whatever ended the session, work landed, and the loop is worth continuing."""
    assert evaluate_unaccounted(4, "changed") == 0


@pytest.mark.parametrize("verdict", ["unchanged", "unknown"])
def test_anything_that_is_not_an_unaccounted_ending_leaves_the_streak_alone(verdict):
    """"Could not tell" and "idle but tidy" are both not "ended badly"."""
    assert evaluate_unaccounted(4, verdict) == 4


def test_the_first_unaccounted_ending_starts_the_streak_at_one():
    assert evaluate_unaccounted(None, "unaccounted") == 1


def test_consecutive_unaccounted_endings_accumulate():
    assert evaluate_unaccounted(4, "unaccounted") == 5


def test_the_two_counters_cannot_disagree_about_what_a_session_was():
    """`evaluate_unaccounted` takes the verdict rather than re-deciding it.

    The property that matters: for any session, exactly one of the two counters
    moves, and which one is decided once. If `evaluate_unaccounted` re-derived
    the verdict from the fingerprints it would be possible for a session to be
    charged to both streaks, or to neither, and the two would tell different
    stories about one run.
    """
    cases = [
        ("a", "b", True),    # changed
        ("a", "a", True),    # unchanged
        ("a", "a", False),   # unaccounted
        (None, "b", True),   # unknown
    ]
    for before, after, accounted in cases:
        idle_before, unacc_before = 4, 4
        idle_after, verdict = evaluate_progress(
            idle_before, before, after, ending_accounted_for=accounted)
        unacc_after = evaluate_unaccounted(unacc_before, verdict)

        idle_moved = idle_after != idle_before
        unacc_moved = unacc_after != unacc_before
        if verdict == "changed":
            assert idle_moved and unacc_moved, "work landing must clear both"
        elif verdict == "unchanged":
            assert idle_moved and not unacc_moved, (
                f"{verdict}: only the idleness streak may move")
        elif verdict == "unaccounted":
            assert unacc_moved and not idle_moved, (
                f"{verdict}: only the unaccounted streak may move")
        else:
            assert not idle_moved and not unacc_moved, (
                f"{verdict}: neither streak may move")


# ── the streaks, run forward ─────────────────────────────────────


def test_an_idle_run_reaches_the_cap_in_exactly_the_documented_number_of_sessions():
    """Three consecutive idle sessions, not two and not four."""
    count = 0
    seen = []
    for _ in range(op.MAX_NOCHANGE_SESSIONS):
        count, verdict = evaluate_progress(count, "same", "same",
                                           ending_accounted_for=True)
        seen.append((count, verdict))
    assert seen[-1][0] == op.MAX_NOCHANGE_SESSIONS
    assert all(v == "unchanged" for _, v in seen)


def test_one_productive_session_resets_a_streak_about_to_trip():
    """The loop must not stop because of what an agent did three sessions ago."""
    count = op.MAX_NOCHANGE_SESSIONS - 1
    count, _ = evaluate_progress(count, "before", "after",
                                 ending_accounted_for=True)
    assert count == 0
    count, _ = evaluate_progress(count, "same", "same", ending_accounted_for=True)
    assert count < op.MAX_NOCHANGE_SESSIONS


def test_a_run_of_killed_sessions_is_bounded_even_though_it_is_never_idle():
    """The reason `unaccounted` is counted separately rather than ignored.

    Sessions nobody saw end never advance the idleness streak, so without this
    second counter a loop whose sessions were all being killed would relaunch
    until `MAX_SESSIONS`.
    """
    idle, unaccounted = 0, 0
    for _ in range(op.MAX_UNACCOUNTED_SESSIONS):
        idle, verdict = evaluate_progress(idle, "same", "same",
                                          ending_accounted_for=False)
        unaccounted = evaluate_unaccounted(unaccounted, verdict)
    assert idle == 0, "killed sessions must not look like idleness"
    assert unaccounted == op.MAX_UNACCOUNTED_SESSIONS, "but they must be bounded"


# ── workspace_fingerprint: what counts as "something changed" ────


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(("git",) + args, cwd=str(cwd), capture_output=True,
                         encoding="utf-8", errors="replace", timeout=60)
    return out.stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with one commit, built without touching the developer's."""
    where = tmp_path / "repo"
    where.mkdir()
    _git("init", "-b", "main", cwd=where)
    _git("config", "user.name", "fixture (agent)", cwd=where)
    _git("config", "user.email", "fixture@example.invalid", cwd=where)
    (where / "file.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=where)
    _git("commit", "-m", "base", cwd=where)
    return where


def test_a_directory_that_is_not_a_repository_cannot_be_fingerprinted(tmp_path):
    """None means "could not answer", which is not "nothing changed"."""
    assert workspace_fingerprint(tmp_path) is None


def test_a_repository_fingerprints_to_something_stable(repo):
    assert workspace_fingerprint(repo) is not None
    assert workspace_fingerprint(repo) == workspace_fingerprint(repo)


def test_a_commit_changes_the_fingerprint(repo):
    before = workspace_fingerprint(repo)
    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "second", cwd=repo)
    assert workspace_fingerprint(repo) != before


def test_an_uncommitted_edit_changes_the_fingerprint(repo):
    """A session that worked but has not committed yet is not idle."""
    before = workspace_fingerprint(repo)
    (repo / "file.txt").write_text("edited\n", encoding="utf-8")
    assert workspace_fingerprint(repo) != before


def test_an_untracked_file_changes_the_fingerprint(repo):
    before = workspace_fingerprint(repo)
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    assert workspace_fingerprint(repo) != before


def test_work_committed_in_a_linked_worktree_changes_the_fingerprint(repo, tmp_path):
    """The case the whole design note is about, and the expensive one to get wrong.

    Work in this project happens on branches in linked worktrees, so a session
    can commit an entire feature without the primary checkout's HEAD or `git
    status` moving at all. A fingerprint scoped to `cwd` would report that
    session as idle, and three of them would stop a loop that was working
    perfectly.

    Committed work is caught by `for-each-ref` -- the branch the worktree is on
    advances -- rather than by the worktree scan. The uncommitted case below is
    the one that proves the scan, and the two mechanisms are why both tests are
    here.
    """
    tree = tmp_path / "linked"
    _git("worktree", "add", "-b", "feature", str(tree), cwd=repo)
    before = workspace_fingerprint(repo)
    (tree / "feature.txt").write_text("feature work\n", encoding="utf-8")
    _git("add", "-A", cwd=tree)
    _git("commit", "-m", "work in the worktree", cwd=tree)
    assert workspace_fingerprint(repo) != before, (
        "a commit in a linked worktree read as no progress")


def test_uncommitted_work_in_a_linked_worktree_changes_the_fingerprint(repo, tmp_path):
    """The half no ref can carry, so only scanning the worktree finds it.

    An agent that has been editing for an hour in a worktree and has not
    committed yet has advanced no ref at all. If the scan skipped linked
    worktrees this would read as an idle session.
    """
    tree = tmp_path / "linked2"
    _git("worktree", "add", "-b", "feature2", str(tree), cwd=repo)
    before = workspace_fingerprint(repo)
    (tree / "scratch.txt").write_text("not committed\n", encoding="utf-8")
    assert workspace_fingerprint(repo) != before


def test_a_commit_on_a_detached_head_changes_the_fingerprint(repo):
    """A detached commit advances no ref, so `for-each-ref` cannot see it.

    `# branch.oid` from `status --porcelain=v2 --branch` is what carries it.
    Without that line the repository fingerprints identically across a session
    that committed real work.
    """
    head = _git("rev-parse", "HEAD", cwd=repo).strip()
    _git("checkout", "--detach", head, cwd=repo)
    before = workspace_fingerprint(repo)
    (repo / "detached.txt").write_text("detached work\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "committed while detached", cwd=repo)
    assert workspace_fingerprint(repo) != before, (
        "a commit made on a detached HEAD read as no progress")


def test_a_branch_deleted_after_pushing_still_leaves_the_work_visible(repo, tmp_path):
    """`refs/remotes` is in scope for exactly this.

    A session that commits, pushes, then deletes its local branch leaves local
    state almost exactly as it found it -- and only the remote-tracking ref
    still records that the work happened. Driven the whole way round, because
    the push alone would prove the easier half.
    """
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)

    before = workspace_fingerprint(repo)
    _git("checkout", "-q", "-b", "throwaway", cwd=repo)
    (repo / "pushed.txt").write_text("work that will be pushed\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "work", cwd=repo)
    _git("push", "-q", "origin", "throwaway", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    _git("branch", "-D", "throwaway", cwd=repo)

    assert workspace_fingerprint(repo) != before, (
        "work that survives only as a remote-tracking ref read as no progress")


def test_a_worktree_whose_directory_is_gone_is_recorded_rather_than_unknown(
        repo, tmp_path):
    """git still lists a worktree whose directory was removed. It cannot be
    holding changes, so it must not make the whole verdict unreadable -- an
    unknown fingerprint stops the breaker advancing at all."""
    import shutil

    tree = tmp_path / "doomed"
    _git("worktree", "add", "-b", "doomed", str(tree), cwd=repo)
    shutil.rmtree(tree)
    assert workspace_fingerprint(repo) is not None, (
        "a removed worktree directory made the repository unmeasurable")


def test_an_unreadable_worktree_makes_the_whole_verdict_unknown(repo, monkeypatch):
    """The worktree that cannot be read is exactly the one that might hold the
    change, so there is no verdict for the repository."""
    monkeypatch.setattr(breakers, "_uncommitted_content", lambda path: None)
    assert workspace_fingerprint(repo) is None


def test_unreadable_refs_make_the_verdict_unknown(repo, monkeypatch):
    monkeypatch.setattr(breakers, "_git_output", lambda *a, **k: None)
    assert workspace_fingerprint(repo) is None


# ── the breaker stopping a real loop ─────────────────────────────
#
# Everything above is about the decision. This is about the consequence, which
# was untested: no test anywhere drove `run_loop_mode` to return
# `EXIT_NO_PROGRESS` or `EXIT_UNACCOUNTED`. A breaker that decides correctly
# and never fires is not a breaker.


@pytest.fixture
def looping(tmp_path, monkeypatch):
    """A supervisor whose sessions are instant, with the breaker armed.

    The sibling fixture in `test_loop_resilience.py` deliberately runs from a
    directory with no git state so the breaker is *inactive* and cannot cut a
    run short while a different counter is under test. This is the other half:
    a fingerprint that reads successfully and never moves, which is what an
    idle agent produces.
    """
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
    # A readable fingerprint that never changes: the breaker is armed, and
    # every session looks like one that did nothing.
    monkeypatch.setattr(op, "workspace_fingerprint", lambda cwd: "unmoving")
    assert op.workspace_fingerprint(workdir) == "unmoving", (
        "the substitution did not reach the module the supervisor reads")
    return tmp_path


def test_the_breaker_bounds_the_healthy_uptime_path(looping, monkeypatch):
    """The bound `test_loop_resilience.py` defers to, which did not exist.

    That file's `test_a_session_that_ran_for_minutes_does_not_count_toward_the
    _give_up_limit` deliberately leaves the healthy-uptime path unbounded --
    a session that stays up past `HEALTHY_SESSION_SECONDS` restarts the crash
    count, so sessions that are healthy but idle can relaunch forever as far as
    *that* counter is concerned. Its docstring says the progress breaker is
    what bounds them and points at a test file that was never written.

    So this is the safety property the loop's own documentation claims and
    nothing checked: a supervisor whose sessions come up, stay up, end
    normally, and change nothing stops on its own.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    ceiling = op.MAX_NOCHANGE_SESSIONS * 10

    def healthy_but_idle(instance, args, session_num, remain_on_exit=False,
                         preamble=""):
        attempts["n"] += 1
        if attempts["n"] > ceiling:
            # The test must not hang if the breaker never fires; failing on the
            # assertion below is a better report than a timeout.
            raise KeyboardInterrupt
        # A restart marker is how a session says it finished and wants the next
        # one: a normal, accounted-for ending rather than a crash.
        instance.restart_marker.touch()

    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", healthy_but_idle)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    rc = op.run_loop_mode(op.Instance("idle-but-healthy"),
                          ["--agent", "test:agent"], is_fresh=True)

    assert rc == op.EXIT_NO_PROGRESS, (
        f"the loop returned {rc} after {attempts['n']} idle sessions; the "
        f"progress breaker never fired")
    assert attempts["n"] == op.MAX_NOCHANGE_SESSIONS, (
        "the breaker fired, but not at the documented number of sessions")


def test_one_productive_session_keeps_the_loop_alive(looping, monkeypatch):
    """The negative control, and the expensive direction to get wrong.

    A breaker that stopped a loop doing real work would be worse than no
    breaker: the failure is silent, and the user finds a fleet that quietly
    retired itself. The same run as above, with one session that moves the
    fingerprint, must outlive the cap.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    beyond_the_cap = op.MAX_NOCHANGE_SESSIONS + 2
    fingerprints = {"value": "unmoving"}
    monkeypatch.setattr(op, "workspace_fingerprint", lambda cwd: fingerprints["value"])

    def works_occasionally(instance, args, session_num, remain_on_exit=False,
                           preamble=""):
        attempts["n"] += 1
        # Land work on the session that would otherwise trip the breaker.
        if attempts["n"] == op.MAX_NOCHANGE_SESSIONS:
            fingerprints["value"] = f"moved-{attempts['n']}"
        if attempts["n"] >= beyond_the_cap:
            instance.stop_marker.touch()
        instance.restart_marker.touch()

    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", works_occasionally)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    rc = op.run_loop_mode(op.Instance("productive"),
                          ["--agent", "test:agent"], is_fresh=True)

    assert attempts["n"] >= beyond_the_cap, (
        f"the loop stopped after {attempts['n']} sessions despite one of them "
        f"changing the workspace")
    assert rc != op.EXIT_NO_PROGRESS


def test_a_run_of_sessions_nobody_saw_end_stops_the_loop(looping, monkeypatch):
    """The other breaker, and the one that catches a fleet being killed.

    These sessions change nothing *and* nobody sees them end -- no handoff, no
    exit code. That is not idleness, so the progress breaker deliberately never
    advances on them; without the second counter the loop would relaunch until
    `MAX_SESSIONS`.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    ceiling = op.MAX_UNACCOUNTED_SESSIONS * 10

    def vanishes(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        if attempts["n"] > ceiling:
            raise KeyboardInterrupt
        # Nothing written: no exit code, no restart marker. The pane was killed.

    really_running = op.is_copilot_running

    def aged(instance):
        # Healthy uptime, so the crash counter keeps resetting and cannot be
        # what stops this run. Only the unaccounted breaker can.
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", vanishes)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    rc = op.run_loop_mode(op.Instance("vanishing"),
                          ["--agent", "test:agent"], is_fresh=True)

    assert rc == op.EXIT_UNACCOUNTED, (
        f"the loop returned {rc} after {attempts['n']} sessions that nobody "
        f"saw end; the unaccounted breaker never fired")
    assert attempts["n"] == op.MAX_UNACCOUNTED_SESSIONS


def test_the_two_breakers_return_different_exit_codes(looping):
    """A postmortem has only the exit code, so the two must not be confusable."""
    assert op.EXIT_NO_PROGRESS != op.EXIT_UNACCOUNTED


# ── the counters as they survive a supervisor ────────────────────
#
# The streaks are re-read from disk at startup (`supervisor.py` lines 217-218),
# because a supervisor can be replaced mid-run -- that is what `restart-loop`
# does to pick up new operator code. An in-memory reset that never reaches disk
# looks correct for the life of one supervisor and stops a productive loop
# early the moment one is replaced.
#
# These were found by a surviving mutant: deleting both `save_*_count(0)` calls
# from the productive-session path changed nothing above, because the in-memory
# counter still reset.


def test_a_productive_session_clears_the_streak_on_disk(looping, monkeypatch):
    """Not just in memory. The next supervisor reads the file, not the variable.

    Observed from inside the following session rather than after the run,
    because a clean stop calls `cleanup_files()` and removes both counters --
    correctly, but it means the end of a run is the one moment the question
    cannot be asked. Mid-run is also exactly when a replacement supervisor
    would read it.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    seen = []
    fingerprints = {"value": "unmoving"}
    monkeypatch.setattr(op, "workspace_fingerprint", lambda cwd: fingerprints["value"])
    productive_at = op.MAX_NOCHANGE_SESSIONS - 1

    def idle_then_productive(instance, args, session_num, remain_on_exit=False,
                             preamble=""):
        attempts["n"] += 1
        # What a supervisor starting right now would inherit.
        seen.append(instance.read_nochange_count())
        if attempts["n"] == productive_at:
            fingerprints["value"] = "moved"
        if attempts["n"] > productive_at + 1:
            instance.stop_marker.touch()
        instance.restart_marker.touch()

    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", idle_then_productive)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    op.run_loop_mode(op.Instance("persists"), ["--agent", "test:agent"],
                     is_fresh=True)

    # The session after the productive one must inherit a cleared streak.
    assert seen[productive_at] == 0, (
        f"the streak on disk was {seen[productive_at]} for the session after "
        f"one that changed the workspace (whole run: {seen}); a replacement "
        f"supervisor would read that and stop a working loop early")


def test_an_idle_session_records_the_streak_on_disk(looping, monkeypatch):
    """The other direction: a supervisor replaced mid-streak must not forget.

    Without this the counter would restart on every `restart-loop`, and a loop
    that was replaced regularly could never reach the cap at all.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    attempts = {"n": 0}
    seen = []

    def idle(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        seen.append(instance.read_nochange_count())
        instance.restart_marker.touch()

    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", idle)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    op.run_loop_mode(op.Instance("remembers"), ["--agent", "test:agent"],
                     is_fresh=True)

    # Session 1 inherits nothing; each later session inherits the count the
    # one before it wrote.
    assert seen[0] in (None, 0), f"a fresh run began with a streak: {seen}"
    assert seen[1:] == list(range(1, len(seen))), (
        f"the streak was not persisted between sessions: {seen}")


def test_a_fresh_run_does_not_inherit_the_previous_runs_streak(looping, monkeypatch):
    """`--fresh` means forget the previous run.

    Inheriting a stalled counter would stop the new run after fewer sessions
    than it is owed -- and the streak it inherited belongs to work somebody
    deliberately abandoned.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(op.time, "time", lambda: clock["t"])

    inst = op.Instance("forgets")
    inst.save_nochange_count(op.MAX_NOCHANGE_SESSIONS - 1)
    assert inst.read_nochange_count() == op.MAX_NOCHANGE_SESSIONS - 1

    attempts = {"n": 0}

    def idle(instance, args, session_num, remain_on_exit=False, preamble=""):
        attempts["n"] += 1
        instance.restart_marker.touch()

    really_running = op.is_copilot_running

    def aged(instance):
        clock["t"] += op.HEALTHY_SESSION_SECONDS + 1
        return really_running(instance)

    monkeypatch.setattr(op, "start_session", idle)
    monkeypatch.setattr(op, "is_copilot_running", aged)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)

    rc = op.run_loop_mode(inst, ["--agent", "test:agent"], is_fresh=True)

    assert rc == op.EXIT_NO_PROGRESS
    assert attempts["n"] == op.MAX_NOCHANGE_SESSIONS, (
        f"a --fresh run stopped after {attempts['n']} sessions; it inherited "
        f"the previous run's streak instead of starting its own")
