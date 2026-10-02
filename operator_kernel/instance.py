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
from presence import path_present
from probes import log, remove_file, utcnow

# ── instance ────────────────────────────────────────────────────
def restart_marker_for(op_id: str) -> Path:
    """The marker `supervisor.py` polls, addressed by operator id.

    A function as well as a property because `operator handoff` is handed an
    id and does not hold an `Instance`. Spelled once so the writer and the
    poller cannot drift.
    """
    return RESTART_DIR / op_id


class Instance:
    """One operator's session plus its state files. The id is the record's."""

    def __init__(self, op_id: str, name: str = ""):
        self.id = op_id
        self.session = op_id
        self.display_name = name or op_id
        # Whether the last launch managed to clear the previous session's
        # exit code. Only `start_session` can know, and only the loop asks;
        # anything that never launches a session has nothing stale to read,
        # which is why the optimistic value is the right default here.
        self.exit_file_cleared = True
        RESTART_DIR.mkdir(parents=True, exist_ok=True)

    # -- file locations
    @property
    def restart_marker(self) -> Path:
        return restart_marker_for(self.id)

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

        ``operator recover`` reads this rather than reconstructing the
        invocation from the launch spec.
        """
        return RESTART_DIR / f"{self.id}.loopargs.json"

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

    def cleanup_files(self) -> None:
        """Drop the live state a clean stop removes. Never the record."""
        for path in (self.restart_marker, self.managed_file, self.spec_file,
                     self.pid_file, self.exit_file, self.session_file,
                     self.loop_pid_file, self.loop_startup_file,
                     self.stop_marker,
                     self.loop_args_file):
            remove_file(path)

    def delete_files(self) -> None:
        """Drop live state and the continuity file. Still not the record."""
        self.cleanup_files()
        remove_file(self.state_file)
        remove_file(RESTART_DIR / f"{self.id}.runner.log")
