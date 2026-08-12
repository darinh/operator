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
from presence import path_present
import instance
import evidence

from config import (LOG_FILE, METRICS_GRACE_SECONDS, MUX, POLL_INTERVAL, SESSION_ID_WAIT, SUPERVISOR_STARTUP_ALLOWANCE)
from presence import dir_present, entry, path_present
from instance import Instance, managed_instances
from mux import MuxError
from probes import _pid_alive, log, remove_file
from supervisor import _spawn_background_loop
from supervisor_records import (_load_loop_args, _running_loop_pid, _supervisor_present, _supervisor_status, _supervisor_where)

@contextmanager
def _exclusive_lock(path: Path):
    """Yield True when this process took ``path`` as a lock, False otherwise.

    ``O_CREAT|O_EXCL`` is the one creation primitive that is atomic on both
    POSIX and Windows, which is what makes it a lock rather than another
    check-then-act. A lock whose recorded pid is *readable* and dead is stale
    -- the holder crashed mid-operation -- and is reclaimed once. A lock whose
    owner cannot be read is not: ``os.open`` creates the file empty and the
    pid lands a moment later, so an unparseable lock is most likely one being
    taken right now, and deleting it would hand the same lock to two
    processes. Refusing there can jam a lock whose holder died inside that
    window; that is the trade, and a jam says so in the log while a double
    acquisition does not.
    """
    acquired = False
    try:
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    recorded = path.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    log(f"  Lock {path.name} exists and could not be read "
                        f"({exc}) — treating it as held")
                    break
                try:
                    holder = int(recorded)
                except ValueError:
                    log(f"  Lock {path.name} names no owner — treating it as "
                        f"held. If nothing is running, remove {path}")
                    break
                if _pid_alive(holder):
                    break
                remove_file(path)
                continue
            except OSError:
                break
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(str(os.getpid()))
                acquired = True
                break
        yield acquired
    finally:
        if acquired:
            remove_file(path)


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


@contextmanager
def _restart_handoff_lock(instance: Instance):
    """Serialise supervisor handoffs for one instance.

    Yields True when the lock was taken, False when another handoff is
    already in progress.
    """
    with _exclusive_lock(instance.restart_lock_file) as acquired:
        yield acquired


def _own_instance_id() -> "str | None":
    """The instance whose Copilot session this process is running inside.

    ``None`` when that cannot be established, which is the answer for a human
    at a terminal and for any process table that could not be read.

    Established by walking our own ancestry and matching it against the Copilot
    pid each instance recorded. The match requires the *ancestor's own name* to
    look like Copilot as well, and that is not belt and braces: pids are
    recycled, and every ancestry contains long-lived shells and multiplexers
    whose pids a dead session's record could collide with. A pid-only test made
    that collision decide which row is called "this session's own".

    Both directions of a wrong answer are bounded, and they are not equally
    bounded, which is why the name check is here. A wrong ``None`` costs only
    the ordering -- the sweep still restarts everything. A wrong *positive*
    costs more: the instance falsely identified is deferred, and the real one
    is left near the front where a catastrophic restart can take this process
    down before it has reported on the rest. It also prints "this session's own
    supervisor" against somebody else's name.
    """
    chain = evidence.ancestry()
    if not chain:
        return None
    mine = {}
    for entry in chain:
        pid = entry.get("pid")
        if pid:
            mine[pid] = ntpath.basename(entry.get("name") or "").lower()
    own = ntpath.basename(sys.executable or "").lower()
    mine.setdefault(os.getpid(), own)
    for ident, meta in managed_instances().items():
        inst = Instance(meta.get("display_name", ident))
        pid = inst.copilot_pid()
        if pid is None or pid not in mine:
            continue
        if not mine[pid].startswith("copilot"):
            # The pid matches an ancestor that is not Copilot, so the record
            # names a process that has died and had its number reissued.
            continue
        return inst.id
    return None


def restart_all_loops() -> int:
    """Replace every running supervisor, leaving each session running.

    The per-instance command is the one that had to exist first, but it is
    almost never what is actually needed: operator code lands on `main` and
    *every* supervisor on the machine is instantly running something that is
    no longer in the tree. `operator list` said so, named all eight, and then
    printed eight commands to type. A remedy that has to be applied by hand
    once per instance is a remedy people apply to some of them.

    Three things this does that a shell loop over the names would not:

    * **It keeps going.** One instance that cannot be restarted -- no recorded
      loop arguments, a session somebody else owns -- must not decide the fate
      of the other seven. Each is attempted and each result is reported.
    * **It does the caller's own instance last.** `restart-loop` deliberately
      leaves the Copilot session running, so an agent restarting its own
      supervisor survives it; but if that one goes wrong the agent is the
      process least able to report it, so it goes after the ones it can still
      speak for.
    * **It reports a census rather than a count.** Which instances were
      restarted, which refused and why. A bare "restarted 6 of 8" leaves the
      reader to work out which two, which is the state that gets ignored.

    The census is taken with ``_supervisor_status`` rather than
    ``_running_loop_pid``, so a supervisor that is still starting is included.
    The narrower probe answered ``None`` for those, and a sweep run seconds
    after a start would have silently skipped exactly the instance most likely
    to need it.
    """
    instances = [inst for inst in active_instances()
                 if _supervisor_status(inst)[0] is not None]
    if not instances:
        print("No loop supervisors are running.")
        return 0

    mine = _own_instance_id()
    ordered = [i for i in instances if i.id != mine]
    ordered += [i for i in instances if i.id == mine]

    print(f"Restarting {len(ordered)} loop supervisor"
          f"{'' if len(ordered) == 1 else 's'}, keeping every session running.")
    failed: list[str] = []
    for inst in ordered:
        label = inst.display_name
        if inst.id == mine:
            print(f"\n── {label} (this session's own supervisor — done last) ──")
        else:
            print(f"\n── {label} ──")
        try:
            rc = restart_loop(label)
        except SystemExit as exc:
            # `die` is reachable from the restart path and would otherwise end
            # the whole sweep on the first instance that refused.
            rc = exc.code if isinstance(exc.code, int) else 1
        except (OSError, MuxError) as exc:
            # And so would a multiplexer that stopped answering, or a state
            # directory that went away underneath one instance. Catching only
            # `SystemExit` covered the refusals this code raises itself and
            # none of the failures the machine raises at it, which is the
            # narrower half of "keeps going".
            print(f"  Failed: {exc}", file=sys.stderr)
            rc = 1
        if rc != 0:
            failed.append(label)

    print()
    done = len(ordered) - len(failed)
    print(f"Restarted {done}/{len(ordered)}.")
    if failed:
        print(f"Not restarted: {', '.join(failed)}")
        print("  Each reported its reason above; they are still running their "
              "old supervisor.")
        return 1
    return 0


def restart_loop(target: str | None) -> int:
    """Replace an instance's loop supervisor, leaving its session running.

    The supervisor is a long-lived process that imported the operator's code
    at startup, so it keeps running the code it started with — `operator stop`
    would pick up new code but takes the Copilot session down with it. This
    swaps only the supervisor: the old one is asked to detach, and a new one
    adopts the still-running session.

    See :func:`restart_all_loops` for the sweep: an operator change makes
    every supervisor on the machine stale at once, so one at a time is the
    exception rather than the normal case.
    """
    if not target:
        print("Usage: operator restart-loop NAME | --all", file=sys.stderr)
        print("Replaces the loop supervisor (picking up new operator code) "
              "without stopping the Copilot session.", file=sys.stderr)
        print("  --all  every running supervisor, which is what an operator "
              "change needs.", file=sys.stderr)
        return 1
    instance = Instance(target)

    if not MUX.has_session(instance.session):
        print(f"No running session '{target}'. Nothing to keep alive — "
              f"start it with: operator --loop --name {target}", file=sys.stderr)
        return 1
    if not instance.owns_live_session():
        print(f"A session named '{instance.session}' is running but was not "
              f"started by this operator. Refusing to touch it.", file=sys.stderr)
        print(f"  Drop stale state with: operator forget {instance.display_name}",
              file=sys.stderr)
        return 1

    # Everything that can be known to be wrong is checked *before* the old
    # supervisor is retired, so a rejected restart leaves the instance exactly
    # as it found it.
    user_args, recorded_cwd = _load_loop_args(instance)
    if recorded_cwd is None:
        print(f"No recorded loop arguments for '{target}'. This instance was "
              f"started by an operator that predates restart-loop.",
              file=sys.stderr)
        print("  Its next session would lose its original arguments, so it is "
              "safer to restart it yourself. Run this from "
              f"{instance.display_name}'s working directory — --adopt is what "
              "keeps the running session alive:", file=sys.stderr)
        print(f"    operator stop-loop {instance.display_name}", file=sys.stderr)
        print(f"    operator --loop --headless --adopt "
              f"--name {instance.display_name} [original args]", file=sys.stderr)
        print("  That records the arguments, so future restarts can just use: "
              f"operator restart-loop {instance.display_name}", file=sys.stderr)
        return 1
    if dir_present(Path(recorded_cwd)) is False:
        # Spawning from the caller's cwd instead would silently point the
        # instance at a different project. A directory that merely cannot be
        # examined is not known to be gone, so it does not earn this refusal.
        print(f"The directory '{target}' was started in no longer exists:",
              file=sys.stderr)
        print(f"  {recorded_cwd}", file=sys.stderr)
        print("  Refusing to restart it somewhere else. Restore the directory, "
              "or stop the instance and start it where you want it.",
              file=sys.stderr)
        return 1

    with _restart_handoff_lock(instance) as acquired:
        if not acquired:
            print(f"Another restart of '{target}' is already in progress.",
                  file=sys.stderr)
            return 1
        return _do_restart_loop(instance, user_args, recorded_cwd)


def _do_restart_loop(instance: Instance, user_args: list[str],
                     recorded_cwd: str) -> int:
    """The handoff itself. Runs holding the per-instance restart lock."""
    target = instance.display_name
    pid, starting = _supervisor_status(instance)
    if pid is not None:
        instance.detach_marker.touch()
        where = _supervisor_where(pid, starting)
        log(f"Restart requested for loop '{target}' ({where})")
        # Budget derived from how long the supervisor can take to look at the
        # marker, so tuning the poll interval cannot silently break this. A
        # supervisor still starting has to finish starting before it polls at
        # all, so the whole window a startup record can be believed for is
        # part of the wait too.
        budget = (SESSION_ID_WAIT + POLL_INTERVAL * 2 + 15
                  + (SUPERVISOR_STARTUP_ALLOWANCE if starting else 0))
        deadline = time.time() + budget
        while time.time() < deadline:
            if _supervisor_present(instance) is None:
                break
            time.sleep(0.5)
        else:
            remove_file(instance.detach_marker)
            print(f"Loop supervisor for '{target}' did not stop within "
                  f"{budget}s. Session left untouched; no new supervisor "
                  f"started.", file=sys.stderr)
            return 1

        # The supervisor is gone, but *why* it went matters. If the detach
        # marker is still sitting there it never consumed our request, so it
        # exited for its own reasons — most likely a concurrent `operator
        # stop`, which also takes the session down. Spawning an adopting
        # supervisor now would resurrect a session the user just stopped.
        # A marker we cannot examine is not proof it was consumed, and the
        # cost of guessing wrong here is a session coming back from the dead,
        # so anything but a definite absence refuses.
        if path_present(instance.detach_marker) is not False:
            remove_file(instance.detach_marker)
            print(f"The supervisor for '{target}' exited without taking the "
                  f"restart request — something else stopped it.",
                  file=sys.stderr)
            print("  Not starting a replacement.", file=sys.stderr)
            return 1
        # Described from what was observed *before* the wait: by now there is
        # no supervisor to describe, and a dead shim pid would otherwise be
        # printed as the one that stopped.
        print(f"Old supervisor ({where}) stopped.")
    else:
        print(f"No supervisor was running for '{target}' — starting one.")

    # Re-check rather than trust the check from before the handoff: `operator
    # stop` may have killed the session while we waited.
    if not MUX.has_session(instance.session):
        print(f"Session '{target}' disappeared during the restart — it was "
              f"stopped by something else. Not starting a replacement.",
              file=sys.stderr)
        return 1

    try:
        _spawn_background_loop(instance, user_args, is_fresh=False, adopt=True,
                               cwd=recorded_cwd)
    except OSError as exc:
        # The old supervisor is already gone, so failing here is the one
        # outcome worth shouting about: the session is alive and unwatched.
        print(f"Could not start a replacement supervisor for '{target}': {exc}",
              file=sys.stderr)
        print(f"  The Copilot session is still running but is NOT supervised.",
              file=sys.stderr)
        print(f"  Retry: operator restart-loop {target}", file=sys.stderr)
        return 1

    # Confirm it actually came up: a supervisor that died on startup would
    # otherwise leave the session unsupervised while we report success. Wait
    # for the pid *file*, not the spawned pid — on Windows sys.executable is
    # often a launcher shim that exits once the real interpreter is running,
    # so the pid Popen hands back may be dead while the supervisor is fine.
    # `_running_loop_pid` and not `_supervisor_present` for the same reason
    # the check is here at all: the startup record is written by the spawn
    # itself, so asking whether one exists would answer yes before the
    # supervisor had executed a single instruction.
    deadline = time.time() + 20
    while time.time() < deadline:
        new_pid = _running_loop_pid(instance)
        if new_pid is not None:
            print(f"✅ Loop supervisor for '{target}' replaced "
                  f"(pid {new_pid}); session kept running.")
            print(f"  Attach: operator join {target}")
            return 0
        time.sleep(0.5)
    print(f"New supervisor for '{target}' did not come up. The Copilot session "
          f"is still running but is no longer supervised.", file=sys.stderr)
    print(f"  Check the log for details: {LOG_FILE}", file=sys.stderr)
    print(f"  Retry: operator restart-loop {target}", file=sys.stderr)
    return 1


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
