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

from config import MUX, RESTART_DIR, UUID_RE
from presence import dir_present, path_present
from operator_mux import safe_instance_id
from probes import log, remove_file, utcnow

# ── instance ────────────────────────────────────────────────────
class Instance:
    """One named unit of work: a session plus its state files."""

    def __init__(self, display_name: str):
        self.display_name = display_name
        self.id = safe_instance_id(display_name)
        self.session = self.id
        # Whether the last launch managed to clear the previous session's
        # exit code. Only `start_session` can know, and only the loop asks;
        # anything that never launches a session has nothing stale to read,
        # which is why the optimistic value is the right default here.
        self.exit_file_cleared = True
        RESTART_DIR.mkdir(parents=True, exist_ok=True)

    # -- file locations
    @property
    def restart_marker(self) -> Path:
        return RESTART_DIR / self.id

    @property
    def state_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.state"

    @property
    def managed_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.managed"

    @property
    def pid_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.pid"

    @property
    def exit_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.exit"

    @property
    def session_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.session"

    @property
    def spec_file(self) -> Path:
        return RESTART_DIR / f"{self.id}.launch.json"

    @property
    def loop_pid_file(self) -> Path:
        """PID of the *background loop supervisor* process (not Copilot's).

        Also carries the stamps that say which *run* of that pid wrote it;
        `_loop_pid_stamp` defines the format and `_running_loop_pid` is the
        only reader that needs more than the first line.
        """
        return RESTART_DIR / f"{self.id}.loop.pid"

    @property
    def loop_startup_file(self) -> Path:
        """A supervisor exists for this instance but has not published its pid.

        The loop pid file cannot answer that question, because it is written
        near the *end* of a startup that takes upwards of 105 ms — and it has
        to stay that way, since every reader of the code record treats it as
        the commit point. So liveness during startup gets its own record,
        written by the spawning parent the instant ``Popen`` returns and
        removed once the pid file exists.

        The file holds one pid, and its mtime bounds how long it may be
        believed without one: on Windows ``sys.executable`` is often a
        launcher shim that re-execs the real interpreter and exits, so the
        pid the parent records can be dead while the supervisor it started is
        perfectly healthy. The child overwrites the record with its own pid as
        its first act, which closes that gap for everything after the import.
        """
        return RESTART_DIR / f"{self.id}.loopstarting"

    @property
    def loop_args_file(self) -> Path:
        """The arguments loop mode was started with.

        Recorded so a supervisor can be replaced (``operator restart-loop``)
        without having to reconstruct them from the launch spec, where they
        are already mixed with the preamble and the flags loop mode adds.
        """
        return RESTART_DIR / f"{self.id}.loopargs.json"

    @property
    def loop_code_file(self) -> Path:
        """Which operator source the running supervisor actually loaded.

        A supervisor is long-lived and imported its code once, at startup, so
        a fix landing afterwards does not reach it (that is what
        ``restart-loop`` is for). Nothing recorded *which* code it started
        with, so neither a person nor the trace could tell a supervisor
        running today's fix from one running last week's — and the records
        both produce are byte-identical in shape.
        """
        return RESTART_DIR / f"{self.id}.loopcode.json"

    @property
    def nochange_file(self) -> Path:
        """Consecutive sessions that left the project's git state untouched.

        On disk rather than in memory because a supervisor can be replaced
        mid-run (``operator restart-loop``), and a breaker that forgets its
        count every time the supervisor is swapped would never trip.
        """
        return RESTART_DIR / f"{self.id}.nochange"

    @property
    def unaccounted_file(self) -> Path:
        """Consecutive sessions that ended unaccounted for and changed nothing.

        Kept apart from ``nochange_file`` rather than sharing its count: two
        killed sessions and one idle one are not three of anything, and
        summing them is what let a loop be retired for idleness it never
        showed. On disk for the same reason as the streak beside it.
        """
        return RESTART_DIR / f"{self.id}.unaccounted"

    @property
    def restart_lock_file(self) -> Path:
        """Held while a supervisor handoff is in progress.

        Two concurrent ``operator restart-loop`` runs would both retire the
        old supervisor and both spawn a replacement, leaving two supervisors
        fighting over one session — each relaunching what the other killed.
        """
        return RESTART_DIR / f"{self.id}.restartlock"

    @property
    def detach_marker(self) -> Path:
        """Touched to ask a running loop supervisor to exit but leave the
        Copilot session running (``operator stop-loop``)."""
        return RESTART_DIR / f"{self.id}.detach"

    @property
    def stop_marker(self) -> Path:
        """Touched to ask a running loop supervisor to shut down *and* stop
        the Copilot session, without racing a relaunch (``operator stop``)."""
        return RESTART_DIR / f"{self.id}.stopreq"

    # -- ownership
    def claim(self, token: str) -> None:
        """Record ownership of the *live* session.

        The record binds a token to the session as it exists now. Continuity
        state (``.state``) deliberately does **not** confer ownership: it
        outlives the session so a named loop can auto-continue, and treating it
        as proof of ownership would let a stale file authorize killing an
        unrelated session that later took the same name.
        """
        payload = {
            "token": token,
            "display_name": self.display_name,
            "session": self.session,
            "claimed_at": utcnow(),
            "pid": os.getpid(),
        }
        tmp = self.managed_file.with_suffix(".managed.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.managed_file)

    def ownership(self) -> dict | None:
        # "Cannot examine" answers the same as "no claim": ownership is what
        # authorizes destroying a session, so anything short of a claim we can
        # actually read must refuse.
        if path_present(self.managed_file) is not True:
            return None
        try:
            return json.loads(self.managed_file.read_text(encoding="utf-8"))
        except ValueError:
            # A legacy or truncated marker: it read fine, it just says
            # nothing. Present but tokenless.
            return {"token": None, "display_name": self.display_name}
        except OSError:
            # Something is there but we could not read it — a dangling
            # symlink, a directory, a denied file. Returning the tokenless
            # dict here would hand out ownership on the strength of a claim
            # nobody managed to read, and ownership is what authorizes
            # killing a session.
            return None

    def owns_live_session(self) -> bool:
        """True only when this operator's claim matches a session that exists.

        Required before any destructive action. ``is_managed`` is about
        continuity, not authority.
        """
        owner = self.ownership()
        if owner is None:
            return False
        if owner.get("session") not in (None, self.session):
            return False
        return MUX.has_session(self.session)

    def is_managed(self) -> bool:
        """True when this instance has operator state of any kind.

        Used for listing and continuity only — never to authorize a kill, so
        state that cannot be examined counts as present: reporting "no such
        instance" for state that is really there is the misleading answer, and
        every destructive path re-checks ownership anyway.
        """
        return (path_present(self.managed_file) is not False
                or path_present(self.state_file) is not False)

    # -- persisted state
    def save_state(self, session_num: int, run_started: str, session_id: str = "") -> None:
        lines = [f"SESSION_NUM={session_num}", f"RUN_STARTED={run_started}"]
        if session_id:
            lines.append(f"COPILOT_SESSION_ID={session_id}")
        tmp = self.state_file.with_suffix(".state.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def load_state(self) -> dict | None:
        if path_present(self.state_file) is False:
            return None
        state: dict[str, str] = {}
        try:
            for line in self.state_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    state[k.strip()] = v.strip()
        except OSError:
            return None
        return state

    def read_session_id(self) -> str:
        try:
            value = self.session_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        return value if UUID_RE.match(value) else ""

    def copilot_pid(self) -> int | None:
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def read_nochange_count(self) -> int | None:
        """Consecutive no-change sessions recorded so far.

        ``None`` means the count could not be established, which is not the
        same as zero: silently reading an unreadable counter as "no evidence
        of stalling yet" is how a circuit breaker ends up permanently off
        without anyone noticing.
        """
        return self._read_streak(self.nochange_file)

    def read_unaccounted_count(self) -> int | None:
        """Consecutive unaccounted-for endings recorded so far.

        Same tri-state as ``read_nochange_count`` and for the same reason.
        """
        return self._read_streak(self.unaccounted_file)

    def _read_streak(self, path: Path) -> int | None:
        present = path_present(path)
        if present is False:
            return 0
        if present is None:
            return None
        try:
            value = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return value if value >= 0 else None

    def save_nochange_count(self, count: int) -> bool:
        """Persist the streak. ``False`` when it could not be written.

        Losing this costs the breaker its memory across a supervisor swap,
        never the running session, so — like ``_save_loop_args`` — it must not
        take an unattended supervisor down with it.
        """
        return self._save_streak(self.nochange_file, count, "no-change")

    def save_unaccounted_count(self, count: int) -> bool:
        """Persist the unaccounted-ending streak. ``False`` when it could not
        be written, on the same terms as ``save_nochange_count``."""
        return self._save_streak(self.unaccounted_file, count, "unaccounted")

    def _save_streak(self, path: Path, count: int, label: str) -> bool:
        tmp = RESTART_DIR / f"{path.name}.tmp"
        try:
            tmp.write_text(f"{count}\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log(f"  Warning: could not record the {label} count: {exc}")
            return False
        return True

    def cleanup_files(self) -> None:
        for path in (self.restart_marker, self.managed_file, self.spec_file,
                     self.pid_file, self.exit_file, self.session_file,
                     self.loop_pid_file, self.loop_startup_file,
                     self.detach_marker, self.stop_marker,
                     self.loop_args_file, self.loop_code_file,
                     self.restart_lock_file,
                     self.nochange_file, self.unaccounted_file):
            remove_file(path)


def read_managed_instances() -> dict[str, dict] | None:
    """Managed instances, or None when the state directory could not be read.

    The distinction matters to anything deciding *who is present*. An empty
    map and a failed listing look identical to a caller and mean opposite
    things, and one of them is a licence to act as though nobody else is
    here.
    """
    found: dict[str, dict] = {}
    present = dir_present(RESTART_DIR)
    if present is None:
        return None
    if not present:
        return found
    try:
        entries = list(RESTART_DIR.iterdir())
    except OSError:
        return None
    for path in entries:
        if path.suffix == ".managed":
            ident = path.name[: -len(".managed")]
        elif path.suffix == ".state":
            ident = path.name[: -len(".state")]
        else:
            continue
        meta = found.setdefault(ident, {})
        if path.suffix == ".managed":
            try:
                meta.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return found


def managed_instances() -> dict[str, dict]:
    """Managed instances as far as they can be listed; unreadable reads empty.

    Only for callers that display or look up a known id. Anything deciding
    whether somebody *else* is here must use :func:`read_managed_instances`
    and refuse on None.
    """
    found = read_managed_instances()
    return {} if found is None else found
