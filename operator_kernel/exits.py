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
import evidence
from paths import project_handoff_file

from config import CATALOG_UNREADABLE, MAX_LAUNCH_FAILURES, OPERATOR_HOME
from presence import path_present
from probes import log
from provenance import running_code_fingerprint

#: A handoff from the previous session is on disk and waiting to be read.
HANDOFF_WAITING = "waiting"
#: No handoff, and one was expected here: the previous session did not reach
#: `handoff`. This is the crash-recovery case.
HANDOFF_MISSING = "missing"
#: Nobody could tell. The catalog would not open, or the probe was denied.
HANDOFF_UNKNOWN = "unknown"
#: The project is not registered, so no handoff could ever have been written
#: here and its absence establishes nothing.
HANDOFF_UNEXPECTED = "unexpected"


class HandoffState:
    """What the launcher established about the waiting handoff, and where.

    A *named* state rather than the boolean this replaces, because the boolean
    could only say "crash recovery" or "not crash recovery" -- and the second
    answer covered four situations that call for four different sentences to
    the agent. Chief among them: **a handoff is sitting on disk right now**.

    That collapse had a cost, paid on 2026-08-15. A session was launched with a
    handoff written six seconds earlier; the preamble said nothing about it,
    because the only branch that says anything is the one for its *absence*. The
    agent ran `operator session start`, read "No assignment" -- an answer about
    work-item claims, not about handoffs -- and inferred there was no handoff to
    read. It then invented a task in a frozen repository and burned the session
    on it. Everything it needed was on disk, and nothing in the text it was
    given contradicted the wrong inference.

    The instruction "always check for a session handoff file" was present and is
    not enough on its own: an agent that skips it produces a transcript
    identical to one that had nothing to read. This is the repository's own
    north star -- a signal indistinguishable from its absence -- so the remedy
    is the one used everywhere else here: say the observed thing out loud, and
    write down that it was said.
    """

    __slots__ = ("verdict", "path", "written")

    def __init__(self, verdict: str, path: "Path | None" = None,
                 written: str = "") -> None:
        self.verdict = verdict
        self.path = path
        self.written = written

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"HandoffState({self.verdict!r}, {self.path!r}, {self.written!r})"


def _written_at(path: Path) -> str:
    """When the handoff was last written, as UTC ISO-8601, or ``""``.

    Reported rather than judged. The protocol is that the *reader* deletes the
    handoff, and nothing enforces it -- so a file still on disk is either a
    handoff nobody has picked up (exactly the case worth announcing loudly) or
    one a previous session read and failed to delete before dying.

    A reviewer proposed treating a handoff older than the previous session as
    absent. That is refused: "the agent did not delete it" is not evidence that
    the agent read it, and suppressing a real handoff silently drops a
    session's accumulated context, which is the harm this whole change exists
    to prevent. Announcing a stale one costs a read; hiding a live one costs
    the work. So the age is handed to the agent, which can see both the
    timestamp and its own clock, instead of being decided here on a guess.

    Never raises: a stat that fails yields no timestamp, and a clause without
    one is still an announcement.
    """
    try:
        return (datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"))
    except (OSError, ValueError, OverflowError):
        return ""


def handoff_state(workdir: Path, instance_id: str = "") -> HandoffState:
    """Establish what is waiting for the session about to launch.

    Every ``return`` names what was observed. In particular a failed probe is
    ``HANDOFF_UNKNOWN`` and never ``HANDOFF_MISSING``: telling an agent its
    predecessor crashed is a claim about the last session, and a probe that
    could not look has established nothing about it.
    """
    handoff_file = project_handoff_file(workdir, instance_id)
    if handoff_file is CATALOG_UNREADABLE:
        # The catalog would not open. That establishes nothing about whether
        # this project is registered, so it must not be reported as either a
        # missing handoff or an unregistered project.
        log("  Could not read the project catalog — not reporting this as "
            "crash recovery")
        return HandoffState(HANDOFF_UNKNOWN)
    if handoff_file is None:
        log("  Project is not registered in the catalog — no handoff file "
            "is expected here")
        return HandoffState(HANDOFF_UNEXPECTED)
    # Probed once and held: asking twice invites the two answers to disagree,
    # and the tri-state exists so the unknown case can be decided deliberately.
    present = path_present(handoff_file)
    if present is None:
        # Telling the agent a handoff is missing is a claim about the last
        # session. A probe that failed has not established anything.
        log(f"  Could not examine {handoff_file} — not reporting this as "
            f"crash recovery")
        return HandoffState(HANDOFF_UNKNOWN, handoff_file)
    if present:
        return HandoffState(HANDOFF_WAITING, handoff_file,
                            _written_at(handoff_file))
    if instance_id:
        legacy = handoff_file.parent.parent / "next-session.md"
        legacy_present = path_present(legacy)
        if legacy_present is None:
            log(f"  Could not examine {legacy} — not reporting this as "
                f"crash recovery")
            return HandoffState(HANDOFF_UNKNOWN, legacy)
        if legacy_present:
            log(f"  No handoff at {handoff_file}, but an unmigrated one is "
                f"at {legacy} — not reporting this as crash recovery")
            return HandoffState(HANDOFF_WAITING, legacy, _written_at(legacy))
    log("  No handoff file found for this project — treating this as "
        "crash recovery")
    return HandoffState(HANDOFF_MISSING, handoff_file)


def crash_recovery_verdict(workdir: Path, instance_id: str = "") -> bool:
    """Did the session before this launch end without leaving a handoff?

    A missing handoff file means the previous session never reached `handoff`
    — most likely a crash (operator itself dying, Windows rebooting, an
    external kill mid-turn) rather than a clean stop. Telling the agent lets
    it act accordingly.

    **This is a claim about one moment and must be re-decided at every
    launch.** It used to be decided once, before the supervisor's loop
    started, and the answer was then baked into a preamble reused by every
    session of the run. A run is long-lived — `copilot-tools` reached session
    #223 on a run started 25 days earlier — so one verdict taken at loop start
    was still being reported to sessions hundreds of handoffs later. It failed
    in both directions: a loop that started with no handoff told every later
    session its predecessor had crashed, contradicting the handoff sitting on
    disk that the agent had just been told to read; and a loop that started
    with one never reported a genuine mid-turn kill afterwards, which is
    precisely the event this note exists to surface. Queued mail was moved to
    per-launch delivery for the same reason and this was left behind.

    An *unregistered* project is a different situation entirely: no catalog
    entry means no handoff file could ever have been written there, so the
    absence proves nothing and must not be reported to the agent as a crash.

    The project-keyed ``next-session.md`` is consulted as a fallback because
    it is what a project that has not yet been through
    ``handoff_tool.migrate_project_handoff`` still has on disk. Migration
    happens on the next *write*, so between this change shipping and this
    instance's next handoff, the instance file legitimately does not exist
    while a real handoff sits beside it. Reporting that as a crash would tell
    the agent its predecessor died in the one situation where the predecessor
    demonstrably did not.

    Reporting is delegated so that this answer and the richer one cannot drift
    apart: two copies of the same branch tree would eventually disagree about
    which situation is a crash, and this predicate is the one that tells an
    agent its predecessor died.
    """
    return handoff_state(workdir, instance_id).verdict == HANDOFF_MISSING


def read_exit_code(instance) -> int | None:
    """The exit code the runner recorded for a session, or ``None``.

    ``None`` covers three things that are worth keeping apart in principle
    and are the same answer here: no file, an empty file, and a file that
    could not be read. What every one of them means to a caller is that
    *nobody observed copilot terminate*. That is the signature of a session
    killed wholesale — the runner dies with it and never gets to write a code
    — as opposed to one whose process ended under a runner that survived to
    write it down.

    That inference is only sound because the runner publishes the code the
    instant ``proc.wait()`` returns, ahead of metrics capture. While the write
    came last, ``None`` also meant "the runner is alive and busy parsing a
    log", which on this machine averaged 95 minutes and reached 13.3 hours —
    so the one fact separating a crash from an external kill was absent for
    most of the window in which somebody would ask. Of 1042 recorded session
    endings, 3 carried a code. Do not move that write back.

    Only call this once the session is gone. `start_session` clears the file
    at launch, but a clearing that failed would let a previous session's code
    be read against a live one, so anything deciding whether *this* session's
    ending was observed must go through :func:`ending_was_observed`.
    """
    try:
        raw = instance.exit_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def ending_was_observed(instance) -> bool:
    """Whether anything outlived this session far enough to record its end.

    An exit code is only evidence about *this* session if the launch managed
    to clear whatever the last one left behind. `start_session` clears the
    file, but the clear is best-effort — a file held open or on a path that
    has gone read-only survives it — and a stale code read against the next
    session would say "somebody watched this end" about the exact case where
    nobody did. Deciding it in the other direction costs a killed session
    being counted against the wrong allowance; deciding it this way costs an
    orderly exit being counted against the unaccounted one, which is bounded
    too. Under a failure that already means the state directory is not
    writable, the second is the one to take.
    """
    if not instance.exit_file_cleared:
        return False
    return read_exit_code(instance) is not None


def _record_session_exit(instance, session_num: int,
                         stop_state, detach_state, restart_state,
                         consecutive: int, uptime: float | None = None,
                         session_gone: bool = True) -> None:
    """Trace a session ending, with the evidence the decision was made on.

    The supervisor polls liveness rather than waiting on the child, so it has
    never had an exit *code* to log -- but the runner writes one to the exit
    file, and that is the difference between "copilot crashed" and "copilot
    shut down cleanly and nobody asked us to expect it". Reading it here costs
    one file read on a path that only runs when a session has already ended.

    ``restart_state`` is passed in rather than probed here. It used to be
    re-read off disk, and the only call site was the branch that had *already*
    established the restart marker was absent -- so the field could not carry
    ``True`` in any record, over 979 recorded exits. A field that cannot vary
    records nothing, and this one was read as proof that no session had ever
    ended by handoff when all it showed was where the call sat.

    It takes the caller's tri-state probe, not a ``bool``. ``marker_set``
    collapses "not there" and "could not look" into one answer, which is the
    right trade for deciding a branch and the wrong one for a record somebody
    will later read as an observation.

    ``session_gone`` is False on the one path that fires while copilot is still
    up (a restart requested mid-session, which is what `handoff` does). No exit
    code can belong to a live process, so none is read: `start_session` clears
    the exit file, but a clearing that failed would otherwise let a previous
    session's code be recorded against this one.
    """
    try:
        code: "int | None" = read_exit_code(instance) if session_gone else None
        try:
            pid = instance.copilot_pid()
        except Exception:
            pid = None
        evidence.record_session_exit(
            OPERATOR_HOME,
            instance=instance.display_name,
            session=session_num,
            pid=pid,
            markers={"stop": stop_state, "detach": detach_state,
                     "restart": restart_state,
                     "exit_code": code,
                     "uptime_s": None if uptime is None else int(uptime)},
            consecutive=consecutive,
            limit=MAX_LAUNCH_FAILURES,
            code=running_code_fingerprint().get("digest"),
        )
    except Exception:
        return
