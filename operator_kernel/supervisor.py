"""Extracted from copilot_operator.py. See docs/spike-extraction.md."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import atexit

from config import (HEALTHY_SESSION_SECONDS, IS_WINDOWS, LAUNCH_BACKOFF_BASE,
                    MAX_LAUNCH_FAILURES, MAX_SESSIONS, MUX, POLL_INTERVAL,
                    RESTART_PAUSE_SECONDS, SESSION_ID_WAIT, UUID_RE)
from exits import (handoff_state, HANDOFF_MISSING, HANDOFF_UNKNOWN,
                   HANDOFF_WAITING)
from instance import Instance
from argtail import before_terminator
from launch import (args_have_explicit_session, extract_agent_from_args,
                    handle_existing_session, has_agent_flag, start_session,
                    with_experimental)
from mux import MuxError
from preamble import build_preamble
from probes import die, log, marker_set, marker_state, remove_file, utcnow
from session_state import is_copilot_running, stop_session_gracefully
from supervisor_records import (_publish_supervisor_records,
                                _record_supervisor_starting, _running_loop_pid)


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
    # No `--agent` unless the caller passed one. `anvil:anvil` was this
    # developer's custom agent, and injecting it made every unconfigured start
    # crash-loop on a machine that only has stock Copilot CLI.
    agent = (extract_agent_from_args(user_args)
             if has_agent_flag(user_args) else "copilot")
    copilot_args += user_args

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
                f"Refusing to adopt it.")
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
        """Sleep in slices so a stop request is noticed promptly.

        The handler sets a flag rather than raising, so a single long sleep
        would delay Ctrl+C by up to a full poll interval. Both markers end the
        sleep too: every caller re-reads them straight after, so a sleep that
        ignored them was a human waiting for nothing -- up to `HELD_PAUSE_CAP`
        of it once an extension could hold a launch.
        """
        end = time.time() + total
        while time.time() < end:
            if (shutdown["requested"] or marker_set(instance.stop_marker)
                    or marker_set(instance.detach_marker)):
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
    workdir = Path.cwd()
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
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
                    launch_args = list(copilot_args)
                    if resume_id:
                        if args_have_explicit_session(launch_args):
                            log("  Skipping automatic --resume; user args already choose a session")
                        else:
                            launch_args = before_terminator(
                                launch_args, [f"--resume={resume_id}"])
                            resume_id_used = resume_id
                        resume_id = ""

                    handoff = handoff_state(workdir, instance.id)
                    launch_preamble = build_preamble(
                        instance,
                        crash_recovery=(had_predecessor
                                        and handoff.verdict == HANDOFF_MISSING),
                        handoff_waiting=(str(handoff.path)
                                         if handoff.verdict == HANDOFF_WAITING
                                         else ""),
                        handoff_unknown=(handoff.verdict == HANDOFF_UNKNOWN),
                        handoff_written=handoff.written)
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
                        # evidence would enter a guess as an observation, and the
                        # postmortem reading it has no way to tell them apart.
                        restart_probe = marker_state(instance.restart_marker)
                        if restart_probe is True:
                            log(f"Session #{session_num}: restart signal detected!")
                            crash_failures = 0
                        else:
                            # No restart was asked for, so the only thing that
                            # can still account for this ending is an exit code:
                            # the runner outlived copilot and wrote one down.
                            # With neither, nobody saw the session end — the
                            # signature of the whole pane being killed — and it
                            # is not chargeable evidence of an idle agent.
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
                    if marker_set(instance.restart_marker):
                        log(f"Session #{session_num}: restart signal detected!")
                        crash_failures = 0
                        # The handoff path arrives here, not above: `handoff`
                        # touches the marker while copilot is still up, so the
                        # supervisor sees the request before it sees the exit.
                        # Recording only the branch above is why every
                        # `session_exit` in the evidence carried `restart=False`
                        # -- not because no session ever ended by handoff, but
                        # because the ones that did were never written down.
                        #
                        # Recorded here rather than after the session is
                        # actually torn down, deliberately. If
                        # `stop_session_gracefully` and the kill behind it both
                        # fail, the supervisor dies -- and a record written
                        # after that point is the one that would never exist.
                        # A evidence saying "a restart was requested" when the
                        # teardown then failed is recoverable by whoever reads
                        # it next; silence about the last thing that happened
                        # before the supervisor died is not.
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
            # interrupted retry does not lose it or skip a number. Zero when
            # nothing has ever launched: the loader adds one, so 0 means
            # "start at #1 next time". The `or 1` that used to be here spent
            # #1 on a session that never existed -- rare when only a failing
            # backend could hold a launch, ordinary now an extension can.
            discovered = instance.read_session_id()
            instance.save_state(
                last_launched or start_session_num - 1,
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

    Re-execs the supervise entry point so the child runs run_loop_mode
    directly instead of recursing into this function again.

    `-m operator_cli.supervise` rather than this file's own path, and that is
    a fix rather than a preference: the spawn used to name `__file__`, and
    when this loop was ported here out of the 9,120-line module the argument
    handling stayed behind in it. Nothing in this file reads `--_supervise`,
    so every supervisor spawned ran a module with no entry point and exited 0
    in silence -- and `restart_loop`, which retires the old supervisor before
    spawning its replacement, left live sessions unsupervised. A module path
    also survives this file being moved, which a `__file__` path does not.

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
    cmd = [sys.executable, "-m", "operator_cli.supervise",
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
