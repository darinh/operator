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

from config import EXIT_GRACE_SECONDS, METRICS_GRACE_SECONDS, MUX
from presence import path_present
from instance import Instance
from mux import MuxError
from probes import log

def pane_program_running(instance: Instance) -> bool:
    """Whether the program the multiplexer launched -- the runner -- is going.

    Two signals, because neither alone answers:

    * ``has_session`` is False once the session is gone, which is how a pane
      started with ``remain_on_exit=False`` ends (single-session mode);
    * ``pane_dead`` is what answers under ``remain_on_exit=True``, which loop
      mode sets: there the session outlives the program in it, so
      ``has_session`` stays true long after the runner has exited and a
      session-only check can never fire.

    Kept as one function because two callers ask this same question for
    different reasons and had drifted apart when it was written out twice:
    :func:`is_copilot_running` adds the exit marker to decide whether *Copilot*
    is up, and :func:`wait_for_metrics_capture` asks it bare, because the
    runner outlives Copilot by however long its metrics capture takes.

    Probe failures propagate. Every caller has to decide what an unanswerable
    question means for it, and they do not agree: the supervisor's poll must
    not read a failed probe as "exited" and tear down a healthy session, while
    a shutdown wait has nothing to gain by asking again.
    """
    if not MUX.has_session(instance.session):
        return False
    return not MUX.pane_dead(instance.session)


def is_copilot_running(instance: Instance) -> bool:
    """True while the session's Copilot process is still alive.

    Three signals, because any one alone can lie:

    * the runner's ``.exit`` marker is authoritative when present. It is
      written the instant ``proc.wait()`` returns, before metrics capture, so
      it is prompt -- but a runner killed alongside its Copilot never reaches
      even that, which is the overwhelmingly common case here;
    * the pane's program still being up, which is the two probes in
      :func:`pane_program_running` and covers both ``remain_on_exit`` modes.

    Only a marker we can actually see ends the session. A probe that fails
    says nothing about whether Copilot is alive, and answering "exited" would
    make the supervisor tear down and relaunch a perfectly healthy session --
    so the failure is left to propagate rather than resolved to a guess here.
    """
    if path_present(instance.exit_file) is True:
        return False
    return pane_program_running(instance)


def _wait_until(satisfied, timeout: float, interval: float) -> bool:
    """Poll ``satisfied`` until it holds or ``timeout`` elapses. True if it did.

    The condition is tested before the deadline, so a wait that is already
    over answers True rather than being reported as a timeout.
    """
    deadline = time.time() + timeout
    while True:
        if satisfied():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)


def wait_for_exit(instance: Instance, timeout: int = EXIT_GRACE_SECONDS) -> bool:
    return _wait_until(lambda: not is_copilot_running(instance), timeout, 1)


def wait_for_metrics_capture(instance: Instance,
                             timeout: int = METRICS_GRACE_SECONDS) -> bool:
    """Wait for the runner to finish capturing metrics, bounded. True if it did.

    The runner publishes its exit marker the instant Copilot terminates and
    captures metrics afterwards -- deliberately, so a dead session is
    relaunched in seconds rather than the 95 minutes measured on this machine.
    The marker therefore no longer implies that session's metrics are in the
    database, and everything that reads the database on the way out has to
    wait for the runner itself, which is :func:`pane_program_running`.

    Every shutdown path waits: the attached single-session summary, and loop
    mode's six endings, three of which destroy the pane immediately afterwards
    and would take an unfinished capture with them. The relaunch path
    deliberately does not -- pausing for a log parse before starting the next
    session is the delay this removed, and a capture cut short there is
    recoverable with `operator ingest` where the wait is not recoverable at
    all.

    Bounded, and the bound is what makes it safe: the capture is a parse of a
    log with no ceiling on its size -- 1.4 GB on this machine, one capture
    measured at 13.3 hours -- so waiting unconditionally would hang a
    terminal. A thin summary is recoverable; a prompt that never returns is
    not.

    A multiplexer that cannot be asked ends the wait rather than spending the
    whole timeout on a question nobody can answer. ``OSError`` as well as
    ``MuxError``: :meth:`Mux.has_session` shells out through ``subprocess.run``
    and raises no ``MuxError`` of its own, so a missing multiplexer binary
    arrives as ``OSError`` alone and catching only the library's exception
    would end an attached session with a traceback instead of a summary.
    """
    try:
        return _wait_until(lambda: not pane_program_running(instance),
                           timeout, 0.5)
    except (MuxError, OSError):
        return False


def stop_session_gracefully(instance: Instance) -> None:
    """Ask Copilot to exit, wait, then force the session down.

    The bash version captured metrics while Copilot was still running, so the
    shutdown telemetry frequently did not exist yet and the record landed as a
    no-op. Here the runner writes metrics once Copilot has actually exited, so
    the only requirement is to end the process cleanly and wait for it.
    """
    if not MUX.has_session(instance.session):
        return
    try:
        MUX.send_keys(instance.session, "/exit")
    except MuxError as exc:
        # Asking politely is best-effort: the session can die between the
        # check above and the keystroke, and a backend that refuses the
        # keystroke has told us nothing except that this route is closed.
        # Fall through to the wait-then-kill path, which is what already
        # happened when the failure went unreported.
        log(f"  Could not send /exit ({exc}) — falling back to the kill path")
    if wait_for_exit(instance, EXIT_GRACE_SECONDS):
        return
    log("  Copilot did not exit within the grace period — terminating session")
    MUX.kill_session(instance.session)
    # Give the runner a moment to finish writing metrics.
    for _ in range(10):
        if path_present(instance.exit_file) is True:
            break
        time.sleep(0.5)
