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
import platform

from mux import Mux
import version

#: The version of the kernel that observed something. Recorded into evidence so
#: a record can be attributed to the code that wrote it -- `landed`,
#: `installed` and `running` are three different states.
TOOLKIT_VERSION = version.__version__




HOME = Path.home()


def operator_home() -> Path:
    """This kernel's state directory, ``~/.operator`` by default.

    ``COPILOT_OPERATOR_HOME`` overrides it, which is how tests relocate the
    whole tree without touching ``Path.home``. In the system this was extracted
    from it lived in ``project_paths`` and was re-exported, because the project
    catalogue needed it and the import ran the other way. The catalogue is not
    part of this kernel, so it lives here now with nothing to re-export it for.
    """
    override = os.environ.get("COPILOT_OPERATOR_HOME")
    return Path(override) if override else Path.home() / ".operator"


class _CatalogUnreadable:
    """Sentinel: the catalog could not be read, which is not "no entry".

    Handed back by ``project_handoff_file`` so a caller cannot mistake "the
    catalog would not open" for "this project was never registered". The two
    licence opposite statements to the agent: the first establishes nothing,
    while the second is used to explain why no handoff is expected here.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<catalog-unreadable>"


class _FileAbsent:
    """A definite answer: the recorded source file is no longer there."""
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "FILE_ABSENT"


POLL_INTERVAL = 10


#: How often the supervisor refreshes the work claim of a running session.
#:
#: The claim's staleness window is measured in minutes and the poll interval
#: in seconds, so writing on every poll would buy no extra evidence and cost
#: a database write per tick for the lifetime of every loop on the machine.
HEARTBEAT_INTERVAL = 60


MAX_SESSIONS = 1000


MAX_LAUNCH_FAILURES = 5


LAUNCH_BACKOFF_BASE = 5


RESTART_PAUSE_SECONDS = 3


# A session that stayed up at least this long before dying did not fail to
# start, so it must not accumulate toward the consecutive-exit limit.
#
# That limit exists to stop a *hot* relaunch spin -- a session that dies on
# startup, every time, forever. It counted exits and never their spacing, so
# five unrelated deaths hours apart retired the supervisor exactly as fast as
# five in a minute. This machine's own logs are the case against it: on four
# separate occasions every instance died within seconds of every other,
# independent of when each was launched, having each run for minutes -- an
# external event, not a crash loop. Five such waves and the user came back to
# nothing running at all. Sessions that were healthy for minutes now reset the
# count, so only genuinely rapid failures can retire a loop.
HEALTHY_SESSION_SECONDS = 120


SESSION_ID_WAIT = 20


EXIT_GRACE_SECONDS = 20


# How long the attached single-session path will wait for the runner to finish
# writing metrics before printing its summary. It is a display concern only --
# the metrics are not lost, just late, and `operator ingest` collects them --
# so this is chosen to be short enough that a terminal always comes back.
# Deliberately not open-ended: the capture is a parse of a Copilot log, and
# those reach 1.4 GB on this machine.
METRICS_GRACE_SECONDS = 15


# How long a supervisor's startup record may be believed on its age alone,
# once the pid it names is no longer alive. It bounds one specific unknown:
# on Windows `sys.executable` is often a launcher shim that re-execs the real
# interpreter and exits, so the pid the spawning parent recorded can be dead
# while the supervisor is starting normally. Generous against a measured
# 105 ms floor for interpreter-plus-import, because everything this bound is
# wrong about costs a bounded wait, while everything it is wrong about in the
# other direction costs a destroyed session.
SUPERVISOR_STARTUP_GRACE = 30.0


# How far *ahead* of the clock a record's mtime may sit and still be read as
# "written just now". A record written microseconds ago routinely reads as
# microseconds in the future: `time.time()` and a filesystem timestamp are not
# the same clock, and on Windows they differ by more than the coarser one's
# tick. Seconds rather than milliseconds so that a filesystem whose timestamps
# round to the nearest second cannot defeat it, and deliberately far below
# SUPERVISOR_STARTUP_GRACE, because this tolerance *adds* to how long a record
# can be believed and every wait that must outlast one pays for it.
#
# Not a way of tolerating a badly wrong clock. A record dated further ahead
# than this is pruned, on purpose: `age < grace` alone stays true of a
# future-dated record for as long as the skew lasts, which is hours, and an
# unbounded refusal is worse than a wrong one here. Declining to start a
# second supervisor is *reported as success*, so it becomes a launch that
# silently starts nothing.
CLOCK_SKEW_TOLERANCE = 2.0


# The longest a startup record can still be believed, measured from now: one
# dated CLOCK_SKEW_TOLERANCE ahead is believed until it is
# SUPERVISOR_STARTUP_GRACE behind. Every wait that has to outlast a record
# adds *this*, never the grace alone.
#
# Derived rather than written out because the two got out of step twice while
# this was being written, and the failure is quiet: a wait shorter than the
# window cannot do anything but time out in exactly the case the window
# exists for, and it strands the marker it laid down when it does.
SUPERVISOR_STARTUP_ALLOWANCE = SUPERVISOR_STARTUP_GRACE + CLOCK_SKEW_TOLERANCE


# The point past which a startup record is not believed even though the pid it
# names is alive. That belief is otherwise unbounded, and a pid is not an
# identity: a supervisor hard-killed inside its startup window -- the one exit
# that runs neither its atexit hook nor its `finally` -- leaves a record behind,
# and the operating system is free to hand that number to something unrelated.
# From then on the record names a live process forever, is never pruned because
# liveness is checked before age, and every later launch declines to start a
# supervisor and *reports success*. A rare crash becomes a permanent, silent
# refusal to run, recoverable only by deleting a file nobody knows exists.
#
# Ten minutes rather than something near the grace because this bound is not
# for slowness -- the grace and a live pid already cover a startup taking its
# time, which is the whole reason liveness is a ground for belief at all. It
# exists so that a phantom expires at all, and a startup still unpublished ten
# minutes in is not one this record should keep speaking for.
#
# Deliberately NOT added to any wait budget. A record with a live process
# behind it cannot be waited out on principle, and a caller that runs into one
# should time out and say so rather than block for ten minutes; the budgets are
# sized for SUPERVISOR_STARTUP_ALLOWANCE, which covers every record whose
# process is gone -- the only kind a wait can outlast.
SUPERVISOR_STARTUP_CEILING = 600.0


# Consecutive sessions that may change nothing before the loop gives up.
MAX_NOCHANGE_SESSIONS = 3


# Consecutive sessions that may end *unaccounted for* -- neither by a restart
# request nor by an exit the runner saw -- and change nothing, before the loop
# gives up.
#
# A separate allowance, and a more patient one, because it is answering a
# different question. Changing nothing is evidence of idleness only when the
# session ended the way the loop expects; a session that was killed at four
# minutes has usually not committed yet, so folding it into the idleness
# streak retires the loops being killed *fastest* -- exactly the ones whose
# failure has nothing to do with the agent. It is still bounded: an
# unattended loop that cannot keep a session alive long enough to produce
# anything burns credits either way, and the healthy-uptime reset means
# MAX_LAUNCH_FAILURES can never bound it. Five matches the tolerance
# MAX_LAUNCH_FAILURES gives deaths, rather than the three idleness gets.
MAX_UNACCOUNTED_SESSIONS = 5


# Seconds any single git probe may take before its answer is "unknown".
GIT_PROBE_TIMEOUT = 30


# run_loop_mode's exit code when the progress circuit breaker stopped it.
EXIT_NO_PROGRESS = 3


# run_loop_mode's exit code when the loop was stopped by sessions that kept
# ending unaccounted for. Distinct from EXIT_NO_PROGRESS because the two carry
# opposite diagnoses -- "the agent has run out of work" versus "something is
# killing the sessions" -- and a reader who cannot tell them apart will act on
# the wrong one.
EXIT_UNACCOUNTED = 4


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


SESSION_ARG_RE = re.compile(r"^--(continue|resume|connect)(=.*)?$")


IS_WINDOWS = platform.system() == "Windows"


# Verdicts of the supervisor-staleness check. Defined up here, away from the
# machinery in `loop_code_state`, because `build_preamble` takes one as a
# default argument and a default is evaluated when the `def` runs -- so the
# name has to exist above the first function that mentions it, not merely
# above the first that calls it.
CODE_CURRENT = "current"


CODE_STALE = "stale"


CODE_UNKNOWN = "unknown"


CODE_UNRECORDED = "unrecorded"


#: The record on disk was not written by the supervisor that is running now.
#:
#: A fifth answer rather than a shade of ``CODE_UNKNOWN``, for the reason
#: `_read_loop_record` keeps "absent" and "could not look" apart: they support
#: different claims and the remedy text differs. This one is a *positive*
#: observation -- we read a record and it names somebody else -- so collapsing
#: it into "cannot tell" would file evidence as an absence of evidence.
#:
#: It also has to be reported, and that is the whole point. Adversarial review
#: caught the first draft returning ``CODE_UNKNOWN`` here, which `operator
#: list` prints nothing for: the row went completely silent, and this item
#: exists because a silent row reads exactly like a healthy one.
CODE_MISMATCH = "mismatch"


#: How many numbered clauses the unconditional part of the preamble already
#: spends, so the optional ones know where to start counting.
#:
#: This is an assumption about prose that lives somewhere else, which is the
#: shape that reads as fact and is checked by nobody. Editing the base text to
#: add a "(6)" would silently make the first optional clause a duplicate,
#: because a wrong number here is still a number and every clause after it
#: stays self-consistently wrong. `test_the_base_clause_count_matches_the_text`
#: counts the clauses in the rendered preamble instead of trusting this, so the
#: assumption is falsified by the text rather than restated by it.
BASE_CLAUSES = 5


# Extra Popen/run kwargs for helper subprocesses that must never show a window.
#
# On Windows, a process that has no console of its own (for example the
# background loop supervisor) makes Windows allocate a brand new *visible*
# console for any console child it starts. CREATE_NO_WINDOW suppresses that.
#
# Constraint: CREATE_NO_WINDOW does not merely hide a window, it gives the
# child a *fresh* invisible console and rebinds its std handles to it. It is
# therefore safe only on calls that pass explicit pipes/handles
# (capture_output=True, stdout=DEVNULL, ...). Never apply it to a spawn that
# has to inherit the caller's terminal, such as an interactive attach or
# anything whose output the user is meant to read -- that output would be
# written into the hidden console and silently lost.
NO_WINDOW_KWARGS: dict = (
    {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    if IS_WINDOWS else {}
)


OPERATOR_HOME = operator_home()


RESTART_DIR = OPERATOR_HOME / "restart"


LOG_FILE = OPERATOR_HOME / "operator.log"




COPILOT_LOG_DIR = Path(
    os.environ.get("COPILOT_LOG_DIR") or HOME / ".copilot" / "logs"
)


MUX = Mux()


#: Paths already reported as unexaminable, so a permanent failure is logged
#: once per process rather than once per poll.
_PROBE_WARNED: set[str] = set()


CATALOG_UNREADABLE = _CatalogUnreadable()


TAB_LOOPING = 3


FILE_ABSENT = _FileAbsent()


_RUNNING_CODE: "dict | None" = None


#: "Nobody has probed this pid yet", as distinct from ``None``, which means
#: "probed, and the OS would not say". The two must not collapse: a caller
#: that has not looked wants the reader to look, and a caller that looked and
#: got nothing must not have the question asked again -- on macOS that is a
#: second ``ps`` fork for an answer already known to be unavailable.
_UNPROBED = object()


#: Key under which the loop pid file carries the supervisor's start token.
LOOP_PID_START_KEY = "pid_start"


#: Key under which it carries the boot the supervisor started in.
LOOP_PID_BOOT_KEY = "boot"
