"""Which operator a process belongs to.

The runner writes one custody file when it starts copilot: the pid, that
pid's start token, and the session number. Handoff may act only for the
operator whose recorded process is an ancestor of the caller and whose
token still matches. A pid is not an identity. Two operators, or none, is
a refusal. This is a check against a confused caller, not a boundary
against other code running as the same user.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from config import RESTART_DIR


@dataclass(frozen=True)
class Custody:
    pid: int
    start: str
    session: int


@dataclass(frozen=True)
class Agent:
    """A process inside an operator's session."""
    record: object
    session: int
    copilot_pid: int


@dataclass(frozen=True)
class Human:
    """A process inside no operator's session. ``shell_pid`` is its parent."""
    shell_pid: int


def file_in(directory: Path, op_id: str) -> Path:
    return Path(directory) / f"{op_id}.custody.json"


def write(path: Path, pid: int, session: int) -> None:
    """Atomically record the process that was just launched.

    The start token is read here, at the moment of the write, so a pid
    recycled before handoff cannot satisfy the file this function leaves.
    """
    from process_identity import process_start_token
    payload = {
        "pid": int(pid),
        "start": process_start_token(pid),
        "session": int(session),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def read(path: Path) -> "Custody | None":
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    start = payload.get("start")
    session = payload.get("session")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(start, str) or not start:
        return None
    if isinstance(session, bool) or not isinstance(session, int) or session <= 0:
        return None
    return Custody(pid, start, session)


def caller(pid: int) -> "Agent | Human | str":
    """Who is running ``pid``: an operator's agent, a person, or why that is unknown.

    A string is the reason, worded to follow ``operator VERB:``.
    """
    import operators
    import process_identity
    import process_tree

    chain = process_tree.ancestry(pid)
    if chain is None:
        return "could not read the process table"
    records = operators.all_operators()
    if records is None:
        return "could not read operators"
    matches = []
    for record in records:
        custody = read(file_in(RESTART_DIR, record.id))
        if custody is None or custody.pid not in chain:
            continue
        live = process_identity.process_start_token(custody.pid)
        if process_identity.same_start_token(custody.start, live) is not True:
            continue
        matches.append(Agent(record, custody.session, custody.pid))
    if not matches:
        return Human(process_tree.shell(chain))
    if len(matches) > 1:
        return "more than one operator matches this process"
    return matches[0]


def identify(pid: int) -> "tuple | str":
    """The one operator this process is inside, or a one-line refusal.

    The tuple is ``(operator, session)``. A string is the refusal, and the
    caller must write nothing.
    """
    found = caller(pid)
    if isinstance(found, Human):
        found = "this process is not inside an operator session"
    if isinstance(found, str):
        return f"operator handoff: {found}"
    return found.record, found.session
