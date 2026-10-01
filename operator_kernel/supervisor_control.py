"""Extracted from copilot_operator.py. See docs/spike-extraction.md."""
from __future__ import annotations

import json
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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from presence import path_present
import instance

from config import (LOG_FILE, METRICS_GRACE_SECONDS, MUX, POLL_INTERVAL, SESSION_ID_WAIT, SUPERVISOR_STARTUP_ALLOWANCE)
from presence import dir_present, entry, path_present
from instance import Instance, managed_instances
from mux import MuxError
from probes import _pid_alive, log, remove_file
from supervisor import _spawn_background_loop
from supervisor_records import (_load_loop_args, _running_loop_pid, _supervisor_present, _supervisor_status, _supervisor_where)

def _request_supervisor_stop(instance: Instance,
                             timeout: float = 20.0 + METRICS_GRACE_SECONDS) -> None:
    """If a background loop supervisor is running for instance, ask it to
    shut down (and take the session with it) before we touch anything else.

    This avoids a race where we kill the mux session ourselves while an
    unrelated background loop is still polling — without this, the
    supervisor would see the session vanish with no stop/restart marker and
    (correctly, in the crash case) relaunch a fresh one right underneath us.

    The marker goes down *before* the check, so a supervisor that becomes
    visible in between still finds it. That ordering is only safe paired with
    the removal below: this function is also called for instances with no
    supervisor at all, and two of ``stop_operator``'s paths return without
    running ``cleanup_files``, so a marker left behind would sit in
    ``RESTART_DIR`` until some future supervisor started and immediately
    stopped itself. The invariant on return is that either a supervisor holds
    the marker, or it is gone because we removed it.

    The default budget carries ``METRICS_GRACE_SECONDS`` for the same reason
    ``_do_restart_loop`` derives its own from ``POLL_INTERVAL``: the
    supervisor's stop branch waits for the runner's metrics capture before it
    exits, so a budget that does not know that expires while the supervisor is
    still doing what it was asked to do. The caller then kills the session
    itself, out from under a supervisor mid-shutdown. Spelled as a sum rather
    than folded into one number so that tuning the wait cannot silently
    un-tune this.
    """
    instance.stop_marker.touch()
    pid, starting = _supervisor_status(instance)
    if pid is None:
        remove_file(instance.stop_marker)
        return
    log(f"  Stop signal sent to loop supervisor for '{instance.display_name}' "
        f"({_supervisor_where(pid, starting)})")
    # A supervisor that has not published yet cannot look at the marker, so a
    # wait shorter than the whole window a record can be believed for is
    # guaranteed to expire before an orphaned one is even eligible to be
    # pruned.
    if starting:
        timeout += SUPERVISOR_STARTUP_ALLOWANCE
    deadline = time.time() + timeout
    while time.time() < deadline and _supervisor_present(instance) is not None:
        time.sleep(0.5)
    # What upholds the invariant in the docstring. Removing unconditionally
    # would reinstate the bug this function exists to fix: a supervisor that
    # is merely slow is still going to read that marker, and the caller is
    # about to kill its session — without the marker it reads that as a crash
    # and relaunches. So the marker is only withdrawn once nothing is there
    # to honour it, which is also the only case where leaving it would strand
    # it for the next supervisor to trip over.
    if _supervisor_present(instance) is None:
        remove_file(instance.stop_marker)


def recoverable_instances() -> list[Instance]:
    """Seats that were being supervised when something stopped them un-cleanly.

    `active_instances` asks who is here *now*, and after a reboot the answer is
    nobody: the multiplexer server is gone and every supervisor pid belongs to
    a previous boot. That is the whole gap this closes. A seat is not a process
    -- it is an identity with a journal, a handoff and a session number that
    accumulate -- and losing the machine should cost it the process, not the
    continuity.

    The discriminator is what a clean stop leaves behind, which is nothing:
    `cleanup_files` removes the ownership claim and the recorded loop
    arguments. A crash, a kill or a power cut removes neither. So a managed
    instance that still has its arguments, with no live session and no live
    supervisor, is one that was running when the machine went down -- and one
    that was stopped on purpose is absent from this list by construction,
    rather than by a flag somebody has to remember to set.
    """
    live = set(MUX.list_sessions()) if MUX.available() else set()
    found: list[Instance] = []
    for ident, meta in sorted(managed_instances().items()):
        inst = Instance(meta.get("display_name", ident))
        if inst.id in live or _running_loop_pid(inst) is not None:
            continue
        _, recorded_cwd = _load_loop_args(inst)
        if recorded_cwd is not None:
            found.append(inst)
    return found


def recover_loop(instance: Instance) -> int:
    """Start a supervisor for a seat whose machine went down under it.

    Not ``--fresh``. Fresh means forget the previous run, which would restart
    the session numbering, discard the resume id and re-arm the breakers that
    were counting. The seat continues: same run, next session, its journal and
    handoff exactly where it left them.
    """
    target = instance.display_name
    user_args, recorded_cwd = _load_loop_args(instance)
    if recorded_cwd is None:
        print(f"No recorded loop arguments for '{target}'. Nothing to recover "
              f"it with.", file=sys.stderr)
        return 1
    if dir_present(Path(recorded_cwd)) is False:
        # Recovering it somewhere else would point the seat at a different
        # project, and its journal is keyed to the one it was working in.
        print(f"The directory '{target}' was working in no longer exists:",
              file=sys.stderr)
        print(f"  {recorded_cwd}", file=sys.stderr)
        return 1
    if MUX.has_session(instance.session):
        print(f"'{target}' has a live session; it did not need recovering.",
              file=sys.stderr)
        return 1
    try:
        _spawn_background_loop(instance, user_args, is_fresh=False,
                               cwd=recorded_cwd)
    except OSError as exc:
        print(f"Could not start a supervisor for '{target}': {exc}",
              file=sys.stderr)
        return 1
    print(f"Recovering '{target}' in {recorded_cwd}")
    return 0


def wait_for_session(instance: Instance, timeout: float = 2.0) -> bool:
    """True when the mux session exists. Brief wait, then the caller joins."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if MUX.available() and MUX.has_session(instance.session):
                return True
        except MuxError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def launch_status(instance: Instance, pid: int,
                  timeout: float = 2.0) -> "tuple[str, int]":
    """(ready|dead|unknown, pid to print).

    Ready is a published loop pid file. The parent writes a startup record
    before the child exists, so that record is not evidence the launch worked.
    Unknown is a live spawn pid with no pid file yet. Dead is a gone spawn pid
    and no pid file.
    """
    deadline = time.monotonic() + timeout
    while True:
        published = _running_loop_pid(instance)
        if published is not None:
            return "ready", published
        if time.monotonic() >= deadline:
            published = _running_loop_pid(instance)
            if published is not None:
                return "ready", published
            if _pid_alive(pid):
                return "unknown", pid
            return "dead", pid
        time.sleep(0.05)


def active_instances() -> list[Instance]:
    """Managed instances with a live session and/or a live loop supervisor.

    A loop between sessions has no session for a few seconds, and a session
    whose loop was stopped has no supervisor. Both are exactly the states a
    user needs to act on, so neither one alone may exclude an instance.
    """
    live = set(MUX.list_sessions()) if MUX.available() else set()
    found: list[Instance] = []
    for ident, meta in sorted(managed_instances().items()):
        inst = Instance(meta.get("display_name", ident))
        if inst.id in live or _running_loop_pid(inst) is not None:
            found.append(inst)
    return found
