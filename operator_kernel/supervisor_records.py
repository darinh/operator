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
import instance
import process_identity

from config import (CLOCK_SKEW_TOLERANCE, LOOP_PID_BOOT_KEY, LOOP_PID_START_KEY, SUPERVISOR_STARTUP_CEILING, SUPERVISOR_STARTUP_GRACE, _UNPROBED)
from instance import Instance
from probes import _pid_alive, log, remove_file
from provenance import _save_loop_code

def _save_loop_args(instance: Instance, user_args: list[str]) -> None:
    """Record how loop mode was invoked so it can be reproduced later."""
    payload = {"user_args": list(user_args), "cwd": str(Path.cwd())}
    tmp = instance.loop_args_file.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, instance.loop_args_file)
    except OSError as exc:
        # Losing this costs a faithful restart-loop, never the running
        # session, so it must not take the supervisor down with it.
        log(f"  Warning: could not record loop args: {exc}")


def _load_loop_args(instance: Instance) -> tuple[list[str], str | None]:
    """The args loop mode was started with, plus its working directory.

    Returns ``([], None)`` when nothing was recorded — the caller decides
    whether that is fatal.
    """
    try:
        payload = json.loads(instance.loop_args_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    args = payload.get("user_args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return [], None
    cwd = payload.get("cwd")
    return args, cwd if isinstance(cwd, str) else None


def _publish_supervisor_records(instance: Instance, user_args: list[str],
                                adopted: bool = False,
                                began_run: bool = True) -> None:
    """Write this supervisor's startup records, pid file last.

    The order is the point, which is why these three writes live in one named
    function instead of inline where they cannot be tested. Every reader of
    the *code record* gates on the loop pid file first — `_instance_summary`
    and `list_instances` both require `snap["loop_pid"]` before they will say
    anything about `loop_code` — so among those readers the pid file is the
    commit point: once it exists, the record describing that supervisor
    already does.

    Written the other way round -- which is how it was -- a concurrent
    ``operator ls`` lands between the pid file and the code record and sees a
    live supervisor that has recorded nothing, which is now a reportable
    state. It would tell a perfectly healthy supervisor, running the newest
    code there is, to restart. The window is short and the consequence is
    only a printed line, but a notice that is sometimes wrong is the kind
    that stops being read, and this one exists precisely because the previous
    one said nothing.

    The args record has a different reader: `restart_loop` gates on the live
    session rather than on the pid file, and refuses when no args are
    recorded. Writing args first shrinks that window too, so the reordering
    is an improvement there rather than a trade.

    What this ordering does **not** do is make a starting supervisor visible.
    The pid file is written near the end of a startup that already takes
    upwards of 105 ms, and until it exists `_running_loop_pid` reports that
    nothing is running. That window was backlog item 0010, and it was not
    fixable by ordering these three writes: it is closed instead by a
    separate record written before this function is reached and removed
    after it -- see `Instance.loop_startup_file` and `_supervisor_present`.
    Anything acting destructively on "is a supervisor running" must ask
    `_supervisor_present`, not `_running_loop_pid`.
    """
    # Recorded so this supervisor can be replaced later without guessing how
    # it was started. Written every time, so it tracks the live invocation.
    _save_loop_args(instance, user_args)
    # ...and which operator source it is actually running, and whether it
    # took over a session rather than starting one. A supervisor keeps the
    # code it imported for the whole run, so this is the only place either
    # answer is still knowable.
    _save_loop_code(instance, adopted=adopted, began_run=began_run)
    _write_loop_pid_file(instance, os.getpid())
    # The pid file now answers the liveness question, so the startup record
    # has nothing left to say. Removed after the pid file exists, never
    # before: the two together are what make the supervisor continuously
    # visible from `Popen` to exit, and a gap between them is the whole bug.
    remove_file(instance.loop_startup_file)


def _loop_pid_stamp(pid: int) -> str:
    """The loop pid file's contents: the pid, plus who that pid *was*.

    Line-oriented, first line the bare decimal pid, and both of those are
    load-bearing. A pid file written by any earlier version of this code is
    exactly that first line, so every stamped reader keeps working on one --
    and a reader that only wants the pid can take `splitlines()[0]` without
    knowing this format exists. The stamps are ``key=value`` lines after it.

    Newline-separated rather than space-separated because a start token may
    contain spaces: `process_identity._ps_start_token` keeps ``ps -o
    lstart=`` verbatim on macOS and BSD, which is a date like
    ``Sat Aug  9 17:25:00 2026``. Splitting that on whitespace would truncate
    the token and make a live supervisor compare unequal to itself.

    Written in the *same* write as the pid, never as a sibling file, and that
    is the whole reason this is one string rather than two paths. A separate
    token file can be a predecessor's while the pid file is this process's --
    a recycled pid plus a failed write is precisely the state being guarded
    against -- and the mismatch would then refute a *live* supervisor. One
    write cannot disagree with itself.

    Either stamp may be absent when the probe cannot answer; see
    `_running_loop_pid` for what a reader is allowed to conclude from that.
    """
    lines = [str(pid)]
    token = process_identity.process_start_token(pid)
    if token:
        lines.append(f"{LOOP_PID_START_KEY}={token}")
    boot = process_identity.boot_identity()
    if boot:
        lines.append(f"{LOOP_PID_BOOT_KEY}={boot}")
    return "\n".join(lines) + "\n"


def _write_loop_pid_file(instance: Instance, pid: int) -> None:
    """Publish the pid file, all of it or none of it.

    Written to a temporary path and renamed over, the way `_save_loop_code`
    already writes the code record. A plain `write_text` truncates first, so a
    concurrent `operator list` can read a file that stops in the middle of the
    start token -- and a *truncated* token is a well-formed one that differs
    from the live process's, which is the one input that would make
    `_loop_pid_reused` delete a running supervisor's pid file. `os.replace` is
    atomic on both POSIX and Windows, so a reader sees the old file or the new
    one and never a half of either.

    Falls back to writing in place if the rename cannot be done, because the
    pid file is what makes a supervisor visible at all: an unwritten one costs
    the session its `stop`, its `restart-loop` and its row in the listing,
    which is worse than the narrow window this avoids.
    """
    text = _loop_pid_stamp(pid)
    tmp = instance.loop_pid_file.with_suffix(".pid.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, instance.loop_pid_file)
    except OSError as exc:
        log(f"  Warning: could not publish the loop pid file atomically: {exc}")
        instance.loop_pid_file.write_text(text, encoding="utf-8")


def _read_loop_pid_stamp(instance: Instance) -> "tuple[int, dict[str, str]] | None":
    """``(pid, stamps)`` from the loop pid file, or ``None`` if there is none.

    ``None`` means "no usable pid file", which is the only thing an absent or
    unreadable one can say. A file whose first line is not an integer is in
    that class too: it names no process, so there is nothing to ask about.

    Read as bytes and decoded a line at a time, because the pid and the
    stamps fail differently. A stamp damaged into invalid UTF-8 is *dropped*
    and the pid still answers -- decoding the whole file would have thrown
    away a perfectly readable pid over an optional field, and a reader that
    finds no pid concludes no supervisor, which is the direction that invites
    a second one. Adversarial review caught the first fix doing exactly that.

    A trailing line with no newline after it is dropped for the same reason:
    a torn write ends mid-line, and a *truncated* token is well-formed -- it
    would compare unequal to the live process and delete a running
    supervisor's pid file. Complete stamps always end in a newline, so
    nothing real is lost. The pid line itself is exempt, because every
    pre-stamp supervisor wrote a bare pid with no newline at all.

    Stamp values are taken verbatim after the first ``=``, unstripped. The
    tokens they carry are already stripped where they are produced, and
    stripping again here would silently rewrite any future token that ended
    in a space -- turning "this is the same process" into "this is a
    different one", which is the expensive direction.
    """
    try:
        data = instance.loop_pid_file.read_bytes()
    except OSError:
        return None
    lines = data.splitlines()
    if not lines:
        return None
    try:
        pid = int(lines[0].decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    stamp_lines = lines[1:]
    if stamp_lines and not data.endswith((b"\n", b"\r")):
        stamp_lines = stamp_lines[:-1]
    stamps: dict[str, str] = {}
    for raw in stamp_lines:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            # A stamp nobody can read is not a stamp saying somebody else
            # holds this pid. Dropped, so the pid falls back to the answer it
            # gave before stamps existed.
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key:
            stamps[key] = value
    return pid, stamps


def _loop_pid_reused(stamps: dict, live_start: "str | None") -> bool:
    """Does ``live_start`` show the pid is held by somebody other than the writer?

    ``True`` only on positive evidence, and the asymmetry is the whole design.
    Answering "yes, reused" for a supervisor that is in fact running is not a
    missing notice, it is a destructive one: `active_instances` drops the
    instance, every notice `_instance_summary` prints is gated on the loop
    pid and so goes silent with it, and `restart-loop` would start a second
    supervisor on top of the first. Answering "no" wrongly leaves the
    pid-reuse blindness that was there before this stamp existed. So anything
    short of evidence -- an unstamped file from an older supervisor, a token
    the OS will not give up, a stamp damaged past reading -- is "no".

    That is deliberately *not* how `_record_describes` treats damage in the
    loop code record, and the two must not be unified without noticing why.
    There, a malformed field costs a staleness verdict and gains a printed
    caveat, so refusing on damage is cheap and safe. Here it costs the
    session's supervisor.

    Both sides must be well-formed tokens of the *same* kind before a
    difference between them counts, which is what
    `process_identity.same_start_token` decides. A value no probe could have
    produced is damage, and damage is not a different process -- it is no
    information at all. Two different kinds are not a difference either: the
    macOS/BSD probe changed its tag from ``ps`` to ``psc`` when it pinned its
    locale and timezone, and comparing the two renderings for equality would
    have deleted the pid file of every macOS supervisor running at the moment
    that landed.

    The live token is passed in rather than probed here, because the caller
    that already has one must not pay for a second: on macOS every probe
    forks ``ps`` with a ten-second timeout, and `instance_snapshot` asks this
    question and then asks `loop_record_facts` a related one about the same
    pid. It is deliberately *not* cached between calls either -- a token
    cached for a pid before a supervisor was spawned onto it compares unequal
    to that supervisor's own stamp, so a stale cache can only ever be wrong in
    the direction that deletes a running supervisor's pid file.

    The boot identity is consulted only when it can discriminate: a token
    that is already an absolute instant cannot collide across a reboot, and
    `process_identity.start_token_is_boot_relative` is what knows which
    shapes are which. Skipping it is a real saving rather than a micro one --
    `boot_identity` forks ``sysctl`` on macOS, and this is on `operator
    list`'s per-instance path and `restart-loop`'s twice-a-second poll.
    """
    recorded_start = stamps.get(LOOP_PID_START_KEY)
    same = process_identity.same_start_token(recorded_start, live_start)
    if same is False:
        return True
    if same is None:
        return False
    # The tokens agree. Only a boot-relative one can still be two processes:
    # `_linux_start_token` counts ticks since boot, so a replacement from
    # another boot can carry its predecessor's exact value.
    if not process_identity.start_token_is_boot_relative(recorded_start):
        return False
    recorded_boot = stamps.get(LOOP_PID_BOOT_KEY)
    if not isinstance(recorded_boot, str) or not recorded_boot:
        return False
    return process_identity.same_boot(
        recorded_boot, process_identity.boot_identity()) is False


def _prune_loop_pid_file(instance: Instance, seen: tuple) -> None:
    """Remove a pid file that no longer describes a supervisor -- if it still
    says what it said when that was decided.

    Deciding costs a process probe, and on macOS that probe can take ten
    seconds. A replacement supervisor can publish its own pid file inside that
    window, and an unconditional unlink would then delete a *live*
    supervisor's file on the strength of a verdict about its predecessor --
    making the replacement invisible and inviting a third one on top of it.
    Re-reading first means the file has to still be the one that was judged.

    It narrows the window rather than closing it: nothing here holds a lock,
    so a publication landing between this read and the unlink is still lost.
    That residual is microseconds of parsing against seconds of probing, and
    closing it properly needs a per-instance lock that no other writer of
    these files takes yet. Found by adversarial review.
    """
    if _read_loop_pid_stamp(instance) != seen:
        return
    remove_file(instance.loop_pid_file)


def _running_loop_identity(instance: Instance) -> "tuple[int | None, object]":
    """``(pid, live_start)`` for the running supervisor, from one probe.

    The token half is what `instance_snapshot` hands to `loop_record_facts`,
    so the listing asks the OS who holds a pid once per instance rather than
    once per question about it. It is :data:`_UNPROBED` when this never
    needed to ask -- an unstamped pid file has nothing to compare against --
    and the record reader then probes for itself.
    """
    parsed = _read_loop_pid_stamp(instance)
    if parsed is None:
        return None, _UNPROBED
    pid, stamps = parsed
    if not _pid_alive(pid):
        _prune_loop_pid_file(instance, parsed)
        return None, _UNPROBED
    live_start: object = _UNPROBED
    if process_identity.is_start_token(stamps.get(LOOP_PID_START_KEY)):
        live_start = process_identity.process_start_token(pid)
    if _loop_pid_reused(stamps, None if live_start is _UNPROBED else live_start):
        # The pid is held by something that is not the supervisor, so this
        # file describes a process that is gone. Pruned for the same reason
        # the dead-pid branch above prunes: left in place it would keep
        # answering, and a recycled pid can outlive the machine's patience.
        _prune_loop_pid_file(instance, parsed)
        return None, _UNPROBED
    return pid, live_start


def _running_loop_pid(instance: Instance) -> int | None:
    """PID of instance's background loop supervisor, if one is alive.

    Prunes the pid file when it no longer describes a running supervisor, so
    callers never have to special-case a stale record.

    Two questions, not one. ``_pid_alive`` asks whether *some* process holds
    the pid, and on Windows -- where pids are recycled aggressively -- an
    unrelated process handed a dead supervisor's pid answers yes. That row
    then prints as ``looping``, with a session number and an age, and is
    byte-identical to a healthy one, which is the silent all-clear this
    instrument exists to stop. `_loop_pid_reused` asks the second question,
    against the start token the supervisor stamped beside its own pid.

    A pid file predating the stamp, or one whose token cannot be checked,
    still answers exactly as it did before: alive means running. Turning
    those into "stopped" would take out `active_instances`, every supervisor
    notice in `_instance_summary`, and `restart-loop`'s refusal to start a
    second supervisor, all at once.

    The half of `_running_loop_identity` that every caller but the listing
    needs; they ask nothing else about the pid, so the token would be a value
    they had to remember not to use.
    """
    return _running_loop_identity(instance)[0]


def _record_supervisor_starting(instance: Instance, pid: int) -> None:
    """Record that a supervisor for ``instance`` exists but is still starting.

    Written twice on purpose. The spawning parent writes it the instant
    ``Popen`` returns, which is the earliest moment anybody knows a
    supervisor exists — earlier than the child can say so itself, because the
    child cannot run a single instruction until the interpreter has started
    and this module has imported. The child then overwrites it with its own
    pid, which is the only pid that stays meaningful on Windows.
    """
    try:
        instance.loop_startup_file.write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        log(f"  Warning: could not record the starting supervisor: {exc}")


def _starting_loop_pid(instance: Instance) -> int | None:
    """PID of a supervisor that exists but has not published its pid file yet.

    ``None`` means no supervisor is starting. Any int means one is, and
    callers must treat it as "present" rather than as a usable pid: ``0`` is
    returned when a supervisor is definitely there but its pid is not
    knowable, which is the honest answer for a record that cannot be read and
    the only one that keeps a destructive caller from concluding absence.

    Two independent grounds for believing the record, because either alone
    fails: the recorded process being alive covers a startup that is taking
    its time, and the record being younger than ``SUPERVISOR_STARTUP_GRACE``
    covers the pid being dead while the supervisor is not — the Windows
    launcher shim exits the moment it has re-execed the real interpreter.
    Requiring both would reopen the window this file exists to close; the
    cost of either is that stop and restart-loop wait, which is bounded and
    reversible, where the cost of concluding absence is a session destroyed
    or a second supervisor started.

    Both grounds are bounded, and neither bound is the other's.
    ``SUPERVISOR_STARTUP_CEILING`` is what stops a live pid being believed
    forever, because a pid is not an identity and the one it names may have
    been reused; see that constant for what an unbounded belief costs.
    """
    path = instance.loop_startup_file
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        # Not "no supervisor" — "no answer". Pruning is not available either
        # (the same path is unexaminable), so this refuses until it clears.
        return 0
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0
    if -CLOCK_SKEW_TOLERANCE <= age:
        # Two independent upper bounds, because the two grounds for belief are
        # worth different amounts of time. A live process is evidence for as
        # long as it lives, up to the ceiling that stops a reused pid speaking
        # forever. An mtime alone is evidence only for the grace.
        if pid > 0 and age < SUPERVISOR_STARTUP_CEILING and _pid_alive(pid):
            return pid
        if age < SUPERVISOR_STARTUP_GRACE:
            return pid
    # Three ways to be outside all of that, and they are the same statement: this
    # record's mtime is no longer evidence that a supervisor is on its way.
    # Above the grace with nothing alive behind it, it is simply old -- one that
    # was going to publish a pid would have by now. Above the ceiling, the pid
    # is alive but the record is far too old to still be about that process.
    # Below the skew tolerance it is dated further ahead
    # than any clock disagreement explains, and believing it would cost an
    # unbounded refusal rather than a bounded one. See the three constants.
    #
    # The future side is inclusive because the tolerance is sized off a
    # filesystem's timestamp granularity, and a filesystem that rounds to 2 s
    # produces an age of *exactly* -2.0 rather than approximately it. A strict
    # bound there would prune every record this instance ever writes, on that
    # filesystem, every time -- reopening the window for the one class of user
    # who cannot see it happening. Costs nothing: the invariant is
    # `believed for <= SUPERVISOR_STARTUP_ALLOWANCE`, and -2.0 is exactly the
    # case that reaches it.
    remove_file(path)
    return None


def _supervisor_status(instance: Instance) -> tuple[int | None, bool]:
    """``(pid, still_starting)`` for this instance, from one pass.

    ``_running_loop_pid`` answers a narrower question — has a supervisor
    finished starting — and every caller that acts destructively on the
    answer needs this one instead. The two were the same function until a
    supervisor's first ~105 ms turned out to be invisible to it, which let
    ``operator stop`` kill a session that a starting supervisor then
    relaunched underneath the user, and ``operator restart-loop`` start a
    second supervisor over the first.

    Both halves come from one pass because they are read from the same two
    files and every caller uses them together. Asking separately means a
    supervisor that publishes its pid, or exits, between the two reads is
    described by one and sized for by the other: a published supervisor that
    exits mid-question gets reported as "still starting" and handed a
    startup-sized budget it has no use for.

    ``still_starting`` is only meaningful when ``pid is not None``; with no
    supervisor at all there is nothing for it to describe.

    Callers that are *confirming a supervisor came up* must keep using
    ``_running_loop_pid``: this is satisfied by the record its own spawn
    wrote, so it would report success before anything had started.
    """
    pid = _running_loop_pid(instance)
    if pid is not None:
        return pid, False
    return _starting_loop_pid(instance), True


def _supervisor_present(instance: Instance) -> int | None:
    """Is *any* supervisor for this instance running, including a starting one?

    The half of ``_supervisor_status`` that most callers need on its own.
    """
    return _supervisor_status(instance)[0]


def _supervisor_where(pid: int | None, still_starting: bool) -> str:
    """How to describe a supervisor whose pid may not mean what it says.

    The pid from ``_supervisor_status`` is only a *running* supervisor's pid
    when it came from the pid file. From a startup record it can be a live
    pid, ``0`` for "not yet knowable", or a launcher shim's pid that is
    already dead — and the last of those is truthy, so
    ``f'pid {pid}' if pid`` reports a dead process as the one in charge,
    for exactly the case the startup record exists to cover.

    Takes both halves rather than re-reading, so what it says cannot
    contradict what its caller decided.
    """
    if not still_starting:
        return f"pid {pid}"
    if not pid:
        return "still starting; pid not yet known"
    return f"still starting; spawned as pid {pid}"
