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
                   HANDOFF_WAITING, restart_claimed)
from instance import Instance
import mail
from argtail import before_terminator
from launch import (args_have_explicit_session, extract_agent_from_args,
                    handle_existing_session, has_agent_flag, start_session,
                    with_experimental)
from mux import MuxError
from preamble import build_preamble
from probes import log, marker_set, marker_state, remove_file, utcnow
from session_state import is_copilot_running, stop_session_gracefully
from supervisor_records import (_publish_supervisor_records,
                                _record_supervisor_starting)


def run_loop_mode(instance: Instance, user_args: list[str], is_fresh: bool) -> int:
    """Supervise an instance, restarting Copilot until asked to stop."""
    # First act, before any work: the pid the spawning parent recorded may be
    # a launcher shim that has already exited, and only this process knows
    # the pid that will still be alive in a second's time. Overwriting also
    # refreshes the record's mtime, so a supervisor that crashes later in
    # startup stops being believed promptly rather than for the full grace.
    _record_supervisor_starting(instance, os.getpid())
    # Registered rather than left to the `finally` below, because refusing a
    # session this operator does not own calls `die()` before that `try` is
    # entered. Without this, a supervisor that correctly refused to start
    # would leave a record making every caller wait out
    # `SUPERVISOR_STARTUP_GRACE` for a process that is already gone.
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
    resume_id = ""
    if not is_fresh:
        state = instance.load_state()
        if state:
            start_session_num = int(state.get("SESSION_NUM", 0) or 0) + 1
            run_started = state.get("RUN_STARTED", run_started)
            candidate = state.get("COPILOT_SESSION_ID", "")
            if UUID_RE.match(candidate or ""):
                resume_id = candidate
                log(f"  Will resume Copilot CLI session: {resume_id}")
            log(f"Continuing from session #{start_session_num} (run started {run_started})")

    # Whether the *previous* session left a handoff behind is a question about
    # a moment, so it is re-asked before every launch rather than answered once
    # here. What is fixed for the whole run is
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
        would delay Ctrl+C by up to a full poll interval. The stop marker ends
        the sleep too: every caller re-reads it straight after, so a sleep that
        ignored it was a human waiting for nothing.
        """
        end = time.time() + total
        while time.time() < end:
            if shutdown["requested"] or marker_set(instance.stop_marker):
                return
            time.sleep(min(0.25, max(0.0, end - time.time())))

    log("═══════════════════════════════════════════")
    log("Copilot CLI Operator starting (loop mode)")
    log(f"  Instance: {instance.display_name}")
    log(f"  Agent: {agent}")
    log(f"  Starting session: #{start_session_num}")
    log(f"  Poll interval: {POLL_INTERVAL}s")
    log(f"  Restart signal: {instance.restart_marker}")
    log(f"  Attach: operator attach {instance.display_name}")
    log("═══════════════════════════════════════════")

    session_num = start_session_num
    last_launched = 0
    launch_failures = 0
    crash_failures = 0
    # When the session now being watched went up. None until one is launched;
    # used to tell a session that died young from one that ran.
    session_started_at: float | None = None
    unknown_markers = 0
    resume_id_used = ""
    _publish_supervisor_records(instance, user_args)
    mail.requeue_stale(instance.id)
    workdir = Path.cwd()
    try:
        try:
            while session_num <= MAX_SESSIONS:
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
                    if marker_set(instance.stop_marker):
                        # A stop request must not wait out session-id
                        # discovery. A session that never reports an id would
                        # hold the stop for the full SESSION_ID_WAIT on top of
                        # the poll interval.
                        break
                    _sleep(1)
                    if shutdown["requested"]:
                        raise KeyboardInterrupt

                restart_requested = False
                while True:
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
                    # Checked before sleeping, not after: `operator stop` blocks
                    # waiting for this supervisor to act, so a whole poll
                    # interval of latency is paid by whoever asked.
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
                    _sleep(POLL_INTERVAL)
                    if shutdown["requested"]:
                        raise KeyboardInterrupt
                    if not is_copilot_running(instance):
                        stop_state = marker_state(instance.stop_marker)
                        if stop_state is None:
                            # The session is gone and we cannot tell whether a
                            # human asked for that. Relaunching would resurrect
                            # a session someone stopped; assuming a stop would
                            # abandon one that crashed. Re-poll instead and let
                            # a readable marker settle it.
                            unknown_markers += 1
                            log(f"Session #{session_num}: copilot is not running but "
                                f"the stop marker cannot be examined "
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
                        # A restart is a claim naming this operator and this
                        # session. An empty marker, a stale session, or a
                        # claim for someone else is not one, and an exit with
                        # no claim is an exit nobody asked for.
                        if restart_claimed(instance.id, session_num):
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
                    if restart_claimed(instance.id, session_num):
                        log(f"Session #{session_num}: restart signal detected!")
                        crash_failures = 0
                        # The handoff path arrives here, not above: `handoff`
                        # writes the marker while copilot is still up, so the
                        # supervisor sees the request before it sees the exit.
                        restart_requested = True
                        break
                    # Only once this launch has reported its session id: the
                    # file is cleared before each launch, and keystrokes sent
                    # before the session is up are lost.
                    if instance.read_session_id():
                        mail.deliver(instance)

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
                           is_fresh: bool, cwd: str | None = None) -> int:
    """Launch the loop supervisor as a detached background OS process.

    Re-execs the supervise entry point so the child runs run_loop_mode
    directly instead of recursing into this function again.

    `-m operator_cli.supervise` rather than this file's own path, and that is
    a fix rather than a preference: the spawn used to name `__file__`, and
    when this loop was ported here out of the 9,120-line module the argument
    handling stayed behind in it. Nothing in this file reads `--_supervise`,
    so every supervisor spawned ran a module with no entry point and exited 0
    in silence, leaving the session unsupervised. A module path also survives
    this file being moved, which a `__file__` path does not.

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
           "--_supervise", "--loop", "--id", instance.id]
    if is_fresh:
        cmd.append("--fresh")
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
    # `operator stop` acts as if no supervisor were running. See
    # `Instance.loop_startup_file`.
    _record_supervisor_starting(instance, proc.pid)
    return proc.pid
