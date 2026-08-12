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

from config import IS_WINDOWS, LOG_FILE, OPERATOR_HOME, _PROBE_WARNED
from presence import path_present

def log(msg: str) -> None:
    line = f"[operator {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        OPERATOR_HOME.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr)


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def remove_file(path: Path) -> bool:
    """Delete ``path`` if we can; report whether it is gone.

    ``unlink(missing_ok=True)`` only forgives a file that was already absent.
    Every caller here is cleaning up state it no longer wants, and a marker
    held open by a scanner or sitting on a denied path is a reason to move on,
    not to end the process with a traceback -- least of all the supervisor's
    shutdown path, which runs when something has already gone wrong.
    """
    try:
        path.unlink()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError as exc:
        log(f"  Could not remove {path.name}: {exc}")
        return False


def marker_state(path: Path) -> bool | None:
    """Tri-state read of a supervisor signal file, warning once per path.

    None means the probe failed: the marker may or may not be set and the
    caller has to decide what to do about not knowing. Most callers can wait
    for the next poll; the one that cannot is crash recovery, which would
    otherwise read "cannot tell" as "nobody asked me to stop".
    """
    present = path_present(path)
    key = str(path)
    if present is None:
        if key not in _PROBE_WARNED:
            _PROBE_WARNED.add(key)
            log(f"  Could not examine {path.name} — treating it as unset and "
                f"re-checking next poll")
        return None
    _PROBE_WARNED.discard(key)
    return bool(present)


def marker_set(path: Path) -> bool:
    """True only when a marker file is definitely there.

    Used by the supervisor for signal files it polls. A probe that cannot
    answer reports "no signal yet" and lets the next poll ask again, which is
    the only outcome that keeps the loop alive; the alternatives are killing
    the supervisor with a traceback or acting on a signal nobody sent.

    Callers that would take an irreversible branch on the False must use
    ``marker_state`` instead: "no marker" and "no answer" only mean the same
    thing when the consequence of being wrong is one more poll.
    """
    return marker_state(path) is True


def _pid_alive(pid: int) -> bool:
    """Cross-platform "is this OS process still alive" check.

    Used for the background loop supervisor's own PID, which is a plain
    Python process — not a mux session or a Copilot child — so none of the
    mux/pane helpers apply.
    """
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
