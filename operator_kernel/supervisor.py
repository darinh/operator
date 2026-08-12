"""Extracted from copilot_operator.py. See docs/spike-extraction.md."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import sqlite3
import signal
import contextlib
import ntpath
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import atexit

from breakers import (evaluate_progress, evaluate_unaccounted, workspace_fingerprint)
from config import (EXIT_NO_PROGRESS, EXIT_UNACCOUNTED, HEALTHY_SESSION_SECONDS, HEARTBEAT_INTERVAL, IS_WINDOWS, LAUNCH_BACKOFF_BASE, MAX_LAUNCH_FAILURES, MAX_NOCHANGE_SESSIONS, MAX_SESSIONS, MAX_UNACCOUNTED_SESSIONS, METRICS_DB, MUX, OPERATOR_HOME, POLL_INTERVAL, RESTART_PAUSE_SECONDS, SESSION_ID_WAIT, TAB_LOOPING, UUID_RE)
from exits import (_record_session_exit, crash_recovery_verdict, ending_was_observed)
from instance import Instance
from launch import (args_have_explicit_session, extract_agent_from_args, handle_existing_session, has_agent_flag, start_session, with_experimental)
from operator_mux import MuxError
from preamble import build_preamble
from probes import die, log, marker_set, marker_state, remove_file, utcnow
from provenance import _launch_code_state, running_code_fingerprint
from session_state import (is_copilot_running, stop_session_gracefully, wait_for_metrics_capture)
from supervisor_records import (_publish_supervisor_records, _record_supervisor_starting, _running_loop_pid)

def _loop_work_db(workdir: Path):
    """The claim/session database for the project being supervised, or ``None``.

    Quiet and total, unlike its CLI equivalent ``_session_db``: the loop must
    launch a session whether or not this project is registered, so every
    failure here becomes ``None`` and a log line rather than an exception.
    Resolved from the *primary* checkout so a loop running inside a worktree
    finds the project's real entry instead of minting a second one.
    """
    try:
        found = catalog_guid(primary_repo_root(workdir))
        if found.guid is None:
            return None
        return operator_session.db_path(project_dir(found.guid))
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not resolve this project's work database ({exc})")
        return None


def _loop_start_session(db, instance: "Instance", session_num: int):
    """Open the session log and settle what this instance is to work on.

    FR-2 wants the assignment resolved before the agent's first token, and the
    only party that can do that is the one launching it. An agent left to work
    it out for itself pays for the reasoning on every session, can still get
    it wrong, and needs the rules in its context permanently to get it right.
    Here it is one query whose answer is already in the preamble.

    Total for the same reason as :func:`_loop_work_db`: a missing assignment
    costs the agent a hint, and must not cost it a session.
    """
    if db is None:
        return None
    try:
        operator_session.init_db(db)
        return operator_session.start_session(
            db, instance=instance.id, session=session_num)
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not resolve this session's assignment ({exc})")
        return None


def _loop_heartbeat(db, instance_id: str) -> None:
    """Refresh whatever claim this instance currently holds.

    The supervisor heartbeats, not the agent. It is the only party that knows
    the session is alive from the process table rather than from the agent's
    opinion of its own progress -- an agent asked to report its own liveness
    reports it right up to the moment it stops being able to, which is the
    only moment the answer mattered.

    The claim is re-read rather than remembered from the assignment, because
    an agent can take one mid-session; caching the item resolved at launch
    would leave exactly those claims un-refreshed until they went stale, and
    the whole point of the cascade is that a stale claim gets taken away.
    """
    if db is None:
        return
    try:
        held = claims.claim_for_instance(db, instance_id)
        if held is not None:
            claims.heartbeat(db, item=held.item, instance=instance_id)
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not refresh this instance's work claim ({exc})")


def run_loop_mode(instance: Instance, user_args: list[str], is_fresh: bool,
                  adopt: bool = False) -> int:
    """Supervise an instance, restarting Copilot until asked to stop.

    ``adopt`` takes over a session that is already running instead of
    launching one. That is what lets a supervisor be replaced — to pick up new
    operator code, say — without disturbing the Copilot session it was
    watching. Everything after the initial launch is identical either way.
    """
    # First act, before any work: the pid the spawning parent recorded may be
    # a launcher shim that has already exited, and only this process knows
    # the pid that will still be alive in a second's time. Overwriting also
    # refreshes the record's mtime, so a supervisor that crashes later in
    # startup stops being believed promptly rather than for the full grace.
    _record_supervisor_starting(instance, os.getpid())
    # Registered rather than left to the `finally` below, because the two
    # startup checks that can end this process -- adoption refusing a session
    # it does not own, and refusing to be a second supervisor -- both call
    # `die()` before that `try` is entered. Without this, a supervisor that
    # correctly refused to start would leave a record making every caller
    # wait out `SUPERVISOR_STARTUP_GRACE` for a process that is already gone,
    # so the obvious retry of `operator restart-loop` would refuse for 30s.
    atexit.register(remove_file, instance.loop_startup_file)
    copilot_args = with_experimental(
        ["--yolo", "--autopilot", "--no-ask-user", "--effort", "high"])
    agent = extract_agent_from_args(user_args)
    if not has_agent_flag(user_args):
        copilot_args += ["--agent", agent]
    copilot_args += user_args

    operator_ingest.init_db(METRICS_DB)

    start_session_num = 1
    run_started = utcnow()
    # Whether *this* supervisor is the one that began the run, recorded rather
    # than later inferred from how far apart two timestamps are. It is only
    # knowable here, and knowing it exactly is what lets `supervisor_took_over`
    # stop guessing -- see `SUPERVISOR_RESTART_MARGIN`, which is the fallback
    # for supervisors that predate this stamp.
    began_run = True
    resume_id = ""
    if not is_fresh:
        state = instance.load_state()
        if state:
            # Adoption joins the session that is already running, so it keeps
            # that session's number. Only a launch moves to the next one.
            start_session_num = int(state.get("SESSION_NUM", 0) or 0) + (0 if adopt else 1)
            if "RUN_STARTED" in state:
                began_run = False
            run_started = state.get("RUN_STARTED", run_started)
            candidate = state.get("COPILOT_SESSION_ID", "")
            if UUID_RE.match(candidate or ""):
                resume_id = candidate
                log(f"  Will resume Copilot CLI session: {resume_id}")
            log(f"Continuing from session #{start_session_num} (run started {run_started})")
    if adopt:
        start_session_num = max(1, start_session_num)
        # Nothing is being launched, so there is nothing to resume into.
        resume_id = ""

    # Whether the *previous* session left a handoff behind is a question about
    # a moment, so it is re-asked before every launch rather than answered once
    # here. See `crash_recovery_verdict`. What is fixed for the whole run is
    # only whether there *was* a predecessor to ask about: at loop start that
    # is exactly "we are continuing an earlier run", and every session this
    # supervisor watches end adds one thereafter.
    #
    # Continuation is read off the session number, not off `resume_id`. A
    # resume id is written only when the previous session reported one and it
    # parses as a UUID, so keying on it would call a run with five sessions
    # behind it a first launch the moment that id went missing -- and the
    # question here is whether a predecessor *existed*, not whether we can
    # resume into it.
    had_predecessor = bool(resume_id) or start_session_num > 1

    if adopt:
        # Refuse to "adopt" anything we do not own or that is not there: the
        # supervisor would otherwise sit polling a session it cannot manage,
        # or immediately relaunch over somebody else's.
        if not MUX.has_session(instance.session):
            die(f"No running session '{instance.display_name}' to adopt.")
        if not instance.owns_live_session():
            die(f"A session named '{instance.session}' is running but was not "
                f"started by this operator. Refusing to adopt it.\n"
                f"  Drop stale state with: operator forget {instance.display_name}")
        # Last line of defence against two supervisors watching one session:
        # they would relaunch over each other's sessions indefinitely. The
        # handoff lock makes this unlikely; this makes it survivable.
        #
        # `_running_loop_pid` and not `_supervisor_present`, but not because
        # the wider reader would be wrong here -- because by this point it
        # would answer the same thing. This process overwrote the startup
        # record with its own pid as its first act, so the record can only
        # name *us*, and a check against it can never fire. What catches a
        # peer that is merely starting is the spawning caller
        # (`restart_loop`, `start_and_attach_loop`, `start_loop_headless`),
        # which consults `_supervisor_present` before deciding to spawn at
        # all. If that claim ever moved to after this guard, the wider reader
        # here would start seeing the record the *parent* wrote for this very
        # child -- a launcher shim's pid on Windows -- and every supervisor
        # would refuse to start itself, on one platform only.
        other = _running_loop_pid(instance)
        if other is not None and other != os.getpid():
            die(f"Another loop supervisor (pid {other}) is already running for "
                f"'{instance.display_name}'. Refusing to start a second one.")
    else:
        handle_existing_session(instance)

    shutdown = {"requested": False}

    def _on_signal(signum, _frame):
        # Handlers only flag intent; blocking work happens on the main path.
        shutdown["requested"] = True

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    def _sleep(total: float) -> None:
        """Sleep in slices so a shutdown request is noticed promptly.

        The handler sets a flag rather than raising, so a single long sleep
        would delay Ctrl+C by up to a full poll interval.
        """
        end = time.time() + total
        while time.time() < end:
            if shutdown["requested"]:
                return
            time.sleep(min(0.25, max(0.0, end - time.time())))

    log("═══════════════════════════════════════════")
    log("Copilot CLI Operator starting (loop mode)")
    log(f"  Instance: {instance.display_name}")
    log(f"  Agent: {agent}")
    log(f"  Starting session: #{start_session_num}")
    log(f"  Poll interval: {POLL_INTERVAL}s")
    log(f"  Restart signal: {instance.restart_marker}")
    log(f"  Attach: operator join {instance.display_name}")
    log("═══════════════════════════════════════════")

    session_num = start_session_num
    last_launched = 0
    launch_failures = 0
    crash_failures = 0
    # When the session now being watched went up. None until one is launched
    # or adopted; used to tell a session that died young from one that ran.
    session_started_at: float | None = None
    unknown_markers = 0
    resume_id_used = ""
    adopting = adopt
    _publish_supervisor_records(instance, user_args, adopted=adopt,
                                began_run=began_run)
    trace.record_supervisor_start(
        OPERATOR_HOME, instance=instance.display_name,
        session=start_session_num, code=running_code_fingerprint())

    # Progress circuit breaker. A fresh run starts a fresh count: --fresh
    # means "forget the previous run", and inheriting its stalled counter
    # would stop the new one after fewer sessions than it is owed.
    workdir = Path.cwd()
    if is_fresh:
        # The count is reset in memory whether or not the file could be
        # removed. Deleting it is disk hygiene; if that fails, reading the
        # stale streak back would let a run started with --fresh stop early,
        # which is exactly what --fresh promises will not happen.
        remove_file(instance.nochange_file)
        nochange = 0
        remove_file(instance.unaccounted_file)
        unaccounted = 0
    else:
        nochange = instance.read_nochange_count()
        unaccounted = instance.read_unaccounted_count()
    if adopt:
        # This supervisor arrived part-way through a session it did not
        # start, so the repository state that session began with is not
        # knowable. Measuring its end against a baseline taken now would read
        # work it had already finished as no work at all, and could stop a
        # loop that had just been productive. The adopted session is
        # unmeasurable by construction; the baseline re-arms from its end.
        baseline = None
    else:
        baseline = workspace_fingerprint(workdir)
    if nochange is None:
        log(f"  Progress breaker: re-arms from the next measurable session "
            f"— cannot read {instance.nochange_file}")
    elif adopt:
        log(f"  Progress breaker: re-arms after the adopted session "
            f"(currently {nochange})")
    elif baseline is None:
        log("  Progress breaker: inactive — no readable git state in "
            f"{workdir}")
    else:
        log(f"  Progress breaker: stops the loop after "
            f"{MAX_NOCHANGE_SESSIONS} consecutive sessions that change "
            f"nothing (currently {nochange})")
        log(f"  Unaccounted endings: stops the loop after "
            f"{MAX_UNACCOUNTED_SESSIONS} consecutive sessions that change "
            f"nothing and end without a handoff or an observed exit "
            f"(currently {'unknown' if unaccounted is None else unaccounted})")
    work_db = None
    last_heartbeat = 0.0
    try:
        try:
            while session_num <= MAX_SESSIONS:
                if adopting:
                    # Take over the session already running: no launch, no
                    # preamble, no resume. Only the first pass adopts; every
                    # session after this one is launched normally.
                    adopting = False
                    log(f"Session #{session_num}: adopting the running session")
                    last_launched = session_num
                    # An adopted session was already up for an unknown time,
                    # which is strictly longer than nothing. Treating it as
                    # started now is the conservative reading: it can only
                    # delay the healthy-uptime reset, never trigger it early.
                    session_started_at = time.time()
                    # An adopted session gets a log row and a heartbeat like
                    # any other. What it does not get is a preamble: nothing
                    # is being launched to read one, so the assignment is
                    # resolved for the record and the claim, not to be said.
                    work_db = _loop_work_db(workdir)
                    _loop_start_session(work_db, instance, session_num)
                    last_heartbeat = 0.0
                else:
                    if marker_set(instance.stop_marker):
                        # A stop request that landed while this supervisor was
                        # still starting. Honoured *before* the launch, not on
                        # the first poll after it: the harm `operator stop`
                        # was reported for is not that the supervisor survives
                        # but that a brand-new agent session gets launched
                        # under someone who asked for everything to stop, and
                        # an agent that runs for two seconds can still commit.
                        remove_file(instance.stop_marker)
                        log(f"Session #{session_num}: stop requested before "
                            f"launch — shutting down without starting one")
                        if MUX.has_session(instance.session):
                            MUX.kill_session(instance.session)
                        instance.cleanup_files()
                        return 0
                    if marker_set(instance.detach_marker):
                        # Same for `operator stop-loop` / the retiring half of
                        # `operator restart-loop`: leave the session alone —
                        # here there is not even one to leave — and exit, so
                        # the caller waiting on this supervisor to go is not
                        # made to wait out a session launch first.
                        remove_file(instance.detach_marker)
                        log(f"Session #{session_num}: detach requested before "
                            f"launch — supervisor exiting")
                        return 0
                    launch_args = list(copilot_args)
                    if resume_id:
                        if args_have_explicit_session(launch_args):
                            log("  Skipping automatic --resume; user args already choose a session")
                        else:
                            launch_args.append(f"--resume={resume_id}")
                            resume_id_used = resume_id
                        resume_id = ""

                    # Messages that arrived while no session was running are
                    # handed over here, per launch rather than once: the base
                    # preamble is built per launch too, so mail that arrives
                    # during session #3 must still reach session #4.
                    # Read now, archive only once the session is really up.
                    # The assignment is settled here, before the preamble is
                    # built, so the agent's first token already knows whether
                    # it is resuming an item, being offered one, or free.
                    work_db = _loop_work_db(workdir)
                    assignment = _loop_start_session(work_db, instance,
                                                     session_num)
                    last_heartbeat = 0.0
                    launch_preamble = build_preamble(
                        agent, instance,
                        crash_recovery=(had_predecessor
                                        and crash_recovery_verdict(
                                            workdir, instance.id)),
                        assignment=assignment,
                        code_state=_launch_code_state())
                    try:
                        waiting = operator_mail.pending(OPERATOR_HOME, instance.id)
                    except operator_mail.MailError as exc:
                        # An unreadable mailbox must not kill an unattended
                        # loop, so this session goes ahead without a mail
                        # preamble -- but it goes ahead having said so. The
                        # messages are neither read nor archived, so they are
                        # offered again at the next launch: a jam that is
                        # announced every session, rather than a delivery that
                        # silently never happens.
                        log(f"  Could not read queued mail ({exc})")
                        log("  Continuing without it; nothing was marked read")
                        waiting = []
                    if waiting:
                        senders = ", ".join(operator_mail.sender_names(waiting))
                        log(f"  Delivering {len(waiting)} queued message(s) from {senders}")
                        launch_preamble += operator_mail.render_for_agent(waiting)

                    # Persist the pending resume id too: if the launch fails or the
                    # process dies here, the id must survive on disk rather than being
                    # cleared by a pre-launch write.
                    instance.save_state(session_num, run_started, resume_id_used)
                    try:
                        start_session(instance, launch_args, session_num,
                                      remain_on_exit=True, preamble=launch_preamble)
                    except MuxError as exc:
                        # A launch failure must not kill an unattended loop. Back off
                        # and retry the same session number rather than exiting.
                        launch_failures += 1
                        log(f"  Launch failed ({exc}) — attempt {launch_failures}")
                        if launch_failures >= MAX_LAUNCH_FAILURES:
                            log(f"  Giving up after {launch_failures} consecutive launch failures")
                            raise
                        if resume_id_used:
                            # Put the resume id back so a failed launch does not lose it.
                            resume_id = resume_id_used
                        backoff = min(60, LAUNCH_BACKOFF_BASE * launch_failures)
                        log(f"  Retrying in {backoff}s...")
                        _sleep(backoff)
                        if shutdown["requested"]:
                            raise KeyboardInterrupt
                        continue
                    launch_failures = 0
                    if waiting:
                        try:
                            operator_mail.archive(OPERATOR_HOME, instance.id,
                                                  [m["id"] for m in waiting])
                        except operator_mail.MailError as exc:
                            # The mail was delivered into the session; only the
                            # bookkeeping failed. Left pending, it is delivered
                            # again next launch -- a duplicate the agent can
                            # see, which is the better failure of the two.
                            log(f"  Delivered mail could not be marked read ({exc})")
                            log("  It will be offered again at the next launch")
                    resume_id_used = ""
                    last_launched = session_num
                    session_started_at = time.time()

                # Record the CLI session id once the runner discovers it.
                for _ in range(SESSION_ID_WAIT):
                    sid = instance.read_session_id()
                    if sid:
                        instance.save_state(session_num, run_started, sid)
                        break
                    if not is_copilot_running(instance):
                        break
                    if marker_set(instance.detach_marker) or marker_set(instance.stop_marker):
                        # A stop/detach request must not wait out session-id
                        # discovery: `operator restart-loop` blocks on this
                        # supervisor exiting, and a session that never reports
                        # an id would hold it for the full SESSION_ID_WAIT on
                        # top of the poll interval.
                        break
                    _sleep(1)
                    if shutdown["requested"]:
                        raise KeyboardInterrupt

                restart_requested = False
                # How the session that is about to end finished, carried to the
                # converged progress check below rather than re-probed there:
                # by then `remove_file` has cleared the restart marker, so the
                # question is no longer answerable from disk. "Accounted for"
                # means a handoff asked for the restart, or the runner survived
                # to write an exit code — either way something explains the
                # ending. A session that simply vanished explains nothing, and
                # a fingerprint that did not move says nothing about idleness.
                ending_accounted_for = False
                while True:
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
                    # Checked before sleeping, not after: `operator stop` and
                    # `operator restart-loop` both block waiting for this
                    # supervisor to act, so a whole poll interval of latency
                    # is paid by a human (or an agent) every time.
                    if marker_set(instance.stop_marker):
                        # `operator stop NAME` asked us to shut down and take the
                        # session with us — same as Ctrl+C, just triggered
                        # remotely since this loop now runs in the background.
                        remove_file(instance.stop_marker)
                        log(f"Session #{session_num}: stop requested — shutting down")
                        stop_session_gracefully(instance)
                        if MUX.has_session(instance.session):
                            MUX.kill_session(instance.session)
                        instance.cleanup_files()
                        return 0
                    if marker_set(instance.detach_marker):
                        # `operator stop-loop NAME` asked us to stop supervising
                        # but leave the session running untouched. Also how
                        # `operator restart-loop` retires the old supervisor.
                        remove_file(instance.detach_marker)
                        sid = instance.read_session_id()
                        instance.save_state(session_num, run_started, sid)
                        log(f"Session #{session_num}: detach requested — leaving "
                            f"session running, supervisor exiting")
                        return 0
                    _sleep(POLL_INTERVAL)
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
                    if not is_copilot_running(instance):
                        stop_state = marker_state(instance.stop_marker)
                        detach_state = marker_state(instance.detach_marker)
                        if stop_state is None or detach_state is None:
                            # The session is gone and we cannot tell whether a
                            # human asked for that. Relaunching would resurrect
                            # a session someone stopped; assuming a stop would
                            # abandon one that crashed. Re-poll instead and let
                            # a readable marker settle it.
                            unknown_markers += 1
                            log(f"Session #{session_num}: copilot is not running but "
                                f"the stop/detach markers cannot be examined "
                                f"({unknown_markers}/{MAX_LAUNCH_FAILURES}) — "
                                f"waiting rather than relaunching")
                            if unknown_markers >= MAX_LAUNCH_FAILURES:
                                log(f"  Giving up after {unknown_markers} consecutive "
                                    f"unreadable checks — leaving the session alone")
                                return 1
                            continue
                        unknown_markers = 0
                        uptime = (None if session_started_at is None
                                  else time.time() - session_started_at)
                        # Probed as a tri-state and recorded as one. `marker_set`
                        # answers False for "not there" and for "could not
                        # look", which is the right call for the *branch* -- one
                        # more poll is cheap -- but writing that False into the
                        # trace would enter a guess as an observation, and the
                        # postmortem reading it has no way to tell them apart.
                        restart_probe = marker_state(instance.restart_marker)
                        if restart_probe is True:
                            log(f"Session #{session_num}: restart signal detected!")
                            crash_failures = 0
                            ending_accounted_for = True
                            _record_session_exit(instance, session_num,
                                                 stop_state, detach_state,
                                                 restart_probe,
                                                 crash_failures, uptime=uptime)
                        else:
                            # No restart was asked for, so the only thing that
                            # can still account for this ending is an exit code:
                            # the runner outlived copilot and wrote one down.
                            # With neither, nobody saw the session end — the
                            # signature of the whole pane being killed — and it
                            # is not chargeable evidence of an idle agent.
                            ending_accounted_for = ending_was_observed(instance)
                            if uptime is not None and uptime >= HEALTHY_SESSION_SECONDS:
                                # Healthy run, then death: whatever killed it,
                                # it is not the startup failure the limit is
                                # counting. Start the count over at this one.
                                if crash_failures:
                                    log(f"  Previous session stayed up "
                                        f"{int(uptime)}s — not a crash loop, "
                                        f"resetting the exit count")
                                crash_failures = 0
                            crash_failures += 1
                            _record_session_exit(instance, session_num,
                                                 stop_state, detach_state,
                                                 restart_probe,
                                                 crash_failures, uptime=uptime)
                            ran_for = ("" if uptime is None
                                       else f" after {int(uptime)}s")
                            log(f"Session #{session_num}: copilot exited unexpectedly"
                                f"{ran_for} "
                                f"({crash_failures}/{MAX_LAUNCH_FAILURES}) — relaunching")
                            if crash_failures >= MAX_LAUNCH_FAILURES:
                                log(f"  Giving up after {crash_failures} consecutive "
                                    f"unexpected exits")
                                instance.cleanup_files()
                                return 1
                        restart_requested = True
                        break
                    # Copilot is confirmed up, so the claim is provably still
                    # being worked. Throttled: the poll interval is seconds and
                    # the staleness window is minutes, so one write per minute
                    # is as much evidence as the cascade can use.
                    if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                        _loop_heartbeat(work_db, instance.id)
                        last_heartbeat = time.time()
                    if marker_set(instance.restart_marker):
                        log(f"Session #{session_num}: restart signal detected!")
                        crash_failures = 0
                        ending_accounted_for = True
                        # The handoff path arrives here, not above: `handoff`
                        # touches the marker while copilot is still up, so the
                        # supervisor sees the request before it sees the exit.
                        # Recording only the branch above is why every
                        # `session_exit` in the trace carried `restart=False`
                        # -- not because no session ever ended by handoff, but
                        # because the ones that did were never written down.
                        #
                        # Recorded here rather than after the session is
                        # actually torn down, deliberately. If
                        # `stop_session_gracefully` and the kill behind it both
                        # fail, the supervisor dies -- and a record written
                        # after that point is the one that would never exist.
                        # A trace saying "a restart was requested" when the
                        # teardown then failed is recoverable by whoever reads
                        # it next; silence about the last thing that happened
                        # before the supervisor died is not.
                        _record_session_exit(
                            instance, session_num,
                            marker_state(instance.stop_marker),
                            marker_state(instance.detach_marker), True,
                            crash_failures,
                            uptime=(None if session_started_at is None
                                    else time.time() - session_started_at),
                            session_gone=False)
                        restart_requested = True
                        break

                if restart_requested:
                    # Something has now ended under this supervisor's watch, so
                    # from here on there is always a predecessor to ask about.
                    had_predecessor = True
                    log("Restarting copilot...")
                    remove_file(instance.restart_marker)
                    stop_session_gracefully(instance)
                    instance.save_state(session_num, run_started)

                    # The session is over and its writes have landed, so this
                    # is the only honest moment to ask whether it changed
                    # anything.
                    current = workspace_fingerprint(workdir)
                    nochange, verdict = evaluate_progress(
                        nochange, baseline, current,
                        ending_accounted_for=ending_accounted_for)
                    unaccounted = evaluate_unaccounted(unaccounted, verdict)
                    if verdict == "unknown":
                        log(f"Session #{session_num}: cannot tell whether "
                            f"anything changed — progress breaker not advanced")
                    elif verdict == "changed":
                        instance.save_nochange_count(0)
                        instance.save_unaccounted_count(0)
                    elif verdict == "unaccounted":
                        # Deliberately not charged to the idleness streak. A
                        # session nobody saw end had usually not committed yet,
                        # so its unchanged fingerprint is a fact about when it
                        # died and not about what the agent was doing.
                        instance.save_unaccounted_count(unaccounted)
                        log(f"Session #{session_num}: changed nothing in "
                            f"{workdir} and ended with no handoff and no "
                            f"observed exit "
                            f"({unaccounted}/{MAX_UNACCOUNTED_SESSIONS}) — not "
                            f"counted as idleness")
                        if unaccounted >= MAX_UNACCOUNTED_SESSIONS:
                            log(f"Loop stopped: {unaccounted} consecutive "
                                f"sessions ended unaccounted for and changed "
                                f"nothing. That is not idleness — something is "
                                f"ending these sessions. Stopping instead of "
                                f"starting session #{session_num + 1}.")
                            log(f"  What ended them: operator trace "
                                f"--kind session_exit")
                            log(f"  Resume with: operator --loop --name "
                                f"{instance.display_name}")
                            instance.cleanup_files()
                            return EXIT_UNACCOUNTED
                    else:
                        instance.save_nochange_count(nochange)
                        log(f"Session #{session_num}: changed nothing in "
                            f"{workdir} ({nochange}/{MAX_NOCHANGE_SESSIONS})")
                        if nochange >= MAX_NOCHANGE_SESSIONS:
                            log(f"Progress breaker tripped: {nochange} "
                                f"consecutive sessions changed nothing. "
                                f"Stopping instead of starting session "
                                f"#{session_num + 1}.")
                            log(f"  Resume with: operator --loop --name "
                                f"{instance.display_name}")
                            instance.cleanup_files()
                            return EXIT_NO_PROGRESS
                    # A session whose end could not be measured keeps the old
                    # baseline, so its work is still counted against the next
                    # comparison rather than being lost between two unknowns.
                    if current is not None:
                        baseline = current

                    session_num += 1
                    log(f"Pausing before session #{session_num}...")
                    _sleep(RESTART_PAUSE_SECONDS)
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
        except KeyboardInterrupt:
            print(file=sys.stderr)
            log("Signal received — shutting down")
            stop_session_gracefully(instance)
            # Record the last session actually launched, not one that never
            # started, and keep whichever resume id is still pending so an
            # interrupted retry does not lose it or skip a number.
            discovered = instance.read_session_id()
            instance.save_state(
                last_launched or start_session_num - 1 or 1,
                run_started,
                discovered or resume_id or resume_id_used,
            )
            if MUX.has_session(instance.session):
                MUX.kill_session(instance.session)
            instance.cleanup_files()
            return 0

        if MUX.has_session(instance.session):
            MUX.kill_session(instance.session)
        instance.cleanup_files()
        log("Operator shut down")
        return 0
    finally:
        remove_file(instance.loop_pid_file)
        remove_file(instance.loop_startup_file)


def _spawn_background_loop(instance: Instance, copilot_args: list[str],
                           is_fresh: bool, adopt: bool = False,
                           cwd: str | None = None) -> int:
    """Launch the loop supervisor as a detached background OS process.

    Re-execs this same script with --_supervise so the child runs
    run_loop_mode directly instead of recursing into this function again.

    Windows note: use CREATE_NO_WINDOW, *not* DETACHED_PROCESS. Both detach
    the child from the parent terminal's console, but DETACHED_PROCESS leaves
    the child with no console at all -- so the moment it (or any descendant)
    starts another console program, Windows allocates a brand new *visible*
    console window for it. That bites immediately here because `sys.executable`
    is typically a venv/Store shim that re-execs the real python.exe as a
    child process. CREATE_NO_WINDOW instead gives the supervisor its own
    console that has no window, and every descendant inherits that invisible
    console, so nothing ever pops up.
    """
    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--_supervise", "--loop", "--name", instance.display_name]
    if is_fresh:
        cmd.append("--fresh")
    if adopt:
        cmd.append("--adopt")
    cmd += copilot_args
    kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, close_fds=True,
                       cwd=cwd or str(Path.cwd()))
    if IS_WINDOWS:
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # decode-ok: every stream is DEVNULL
    # The earliest anyone can know this supervisor exists. The child cannot
    # say so for itself until the interpreter has started and this module has
    # imported -- a measured 105 ms floor -- and until something says so,
    # `operator stop` and `operator restart-loop` both act as if no
    # supervisor were running. See `Instance.loop_startup_file`.
    _record_supervisor_starting(instance, proc.pid)
    return proc.pid
