"""The evidence log: who ran, how it ended, and what code observed it.

Extracted from a 21-definition evidence module; the kernel uses three of
them. `record_session_exit` is the one that matters -- the record whose
earlier version could not express the event it existed to detect.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from config import TOOLKIT_VERSION


IS_WINDOWS = os.name == "nt"


#: Rotate at this size. One line is a few hundred bytes, so this is on the
#: order of a hundred thousand invocations -- long enough to cover an incident
#: that unfolded over days, short enough not to become the largest file in
#: the directory.
_MAX_BYTES = 8 * 1024 * 1024


def trace_path(operator_home: Path) -> Path:
    return Path(operator_home) / "trace.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _win_process_table() -> "dict[int, tuple[int, str]] | None":
    """``{pid: (ppid, exe_name)}`` for every visible process, or ``None``.

    Uses the ToolHelp snapshot, which needs no special privilege and is a
    single call for the whole table -- one syscall beats walking ``ps`` per
    generation, and this runs on every operator invocation.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declared, not inferred. ctypes defaults every restype to `c_int`,
        # which on 64-bit Windows truncates a HANDLE and — worse — turns the
        # INVALID_HANDLE_VALUE returned by a failed snapshot into -1, so the
        # guard below would compare it against c_void_p(-1) (2**64-1), miss,
        # and walk on with a handle that was never valid.
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                     wintypes.DWORD]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            table: dict[int, tuple[int, str]] = {}
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID), str(entry.szExeFile))
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
            # `table`, not `table or None`: the snapshot was taken, so an empty
            # result is something we read, not something we failed to read.
            return table
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return None


def _win_image_path(pid: int) -> "str | None":
    """Full executable path for ``pid``, or ``None`` if it cannot be read.

    The ToolHelp table gives only a bare filename, and a bare filename is not
    enough to tell two launchers apart: on the machine this module was written
    for, the third-party supervisor and the operator's own children were both
    ``python.exe``, and only the path distinguished them. Failure here is
    ordinary -- protected and elevated processes refuse the handle -- so it is
    reported as ``None`` rather than as an empty string that would read like
    a process with no path.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        # PROCESS_QUERY_LIMITED_INFORMATION: the weakest right that answers
        # this question, so it succeeds for processes we may not open fully.
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)):
                return None
            return buf.value or None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


class _TreeUnreadable(Exception):
    """Raised when the process tree could not be read, as distinct from read
    and found empty. It exists so a permission failure cannot arrive at
    ``classify`` wearing the same clothes as a genuine dead end."""


def _posix_parent(pid: int) -> "tuple[int, str] | None":
    """``(ppid, command)`` for ``pid`` on Linux.

    ``None`` means the process is gone -- a real dead end. A tree that could
    not be read raises ``_TreeUnreadable`` instead, because those two answers
    lead to opposite conclusions and ``None`` for both would let "we were not
    allowed to look" be reported as "there is nothing above this process".
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8",
                                                   errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _TreeUnreadable(str(exc)) from exc
    # The comm field is parenthesised and may itself contain spaces and
    # parentheses, so the fields after it are found from the LAST ')'.
    close = stat.rfind(")")
    if close == -1:
        return None
    rest = stat[close + 2:].split()
    if len(rest) < 2:
        return None
    try:
        ppid = int(rest[1])
    except ValueError:
        return None
    name = ""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        name = raw.split(b"\0")[0].decode("utf-8", "replace")
    except OSError:
        pass
    if not name:
        open_paren = stat.find("(")
        if open_paren != -1 and close > open_paren:
            name = stat[open_paren + 1:close]
    return ppid, name


def _ps_process_table() -> "dict[int, tuple[int, str]] | None":
    """``{pid: (ppid, command)}`` via one ``ps`` call, for macOS and BSD."""
    try:
        import subprocess
        proc = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,comm="],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    table: dict[int, tuple[int, str]] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]),
                                    parts[2] if len(parts) > 2 else "")
        except ValueError:
            continue
    # `table`, not `table or None`: ps ran and exited 0, so an empty result is
    # an answer we read rather than a failure to read one.
    return table


def _procfs_available() -> bool:
    """True when Linux's ``/proc`` can be used for the ancestry walk.

    A wrong ``False`` is harmless here, and that is the reason this probe is
    allowed to be two-valued: the caller falls through to the ``ps`` table,
    which answers the same question on every platform that has ``/proc``. The
    failure that matters in this module is a wrong *answer about ancestry*,
    and both branches lead to one that is either right or explicitly ``None``.
    """
    try:
        return Path("/proc").is_dir()  # probe-ok: a wrong False falls back to ps
    except OSError:
        return False


def record_mandate_read(operator_home: Path, *, instance: str, session: int,
                        mandate=None) -> None:
    """Record the authority a session was launched with. Never raises.

    A `fact.*`, not a claim: the supervisor read a file and hashed it, which
    is an observation about its own behaviour.

    This exists because the kernel cannot *prevent* a mandate being edited. A
    seat runs under the owner's filesystem identity, so it can write the file
    that says what it may do -- and that gap does not close until agents get
    their own OS account, which is the open question in `docs/plan.md`. What
    the kernel can do meanwhile is make the edit *visible*: every launch
    records the digest of the text it used, so a mandate that changes shows up
    as a change in the ledger, with the session that ran under each version
    named beside it.

    That is a weaker guarantee than prevention and is deliberately not
    described as anything else. It converts a silent rewrite into one that can
    be found afterwards, which is exactly the difference backlog 0013 turned
    on: the sentence was discoverable only because git had kept it.
    """
    try:
        _append(trace_path(Path(operator_home)), {
            "ts": _utcnow(),
            "event": "mandate_read",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            # None rather than a placeholder digest: "no mandate" and "a
            # mandate whose text happens to be empty" are different states,
            # and only the first means the session was told it had no grant.
            "present": mandate is not None,
            "author": getattr(mandate, "author", None),
            "recorded": getattr(mandate, "recorded", None),
            "source": getattr(mandate, "source", None),
            "digest": getattr(mandate, "digest", None),
        })
    except Exception:
        return
def ancestry(pid: "int | None" = None,
             limit: int = 12) -> "list[dict] | None":
    """The process chain above ``pid``, nearest parent first.

    Returns ``None`` when the process table could not be read at all. That is
    the whole point of the return type: an empty list is a statement that this
    process has no ancestors, which is never true, and a caller that cannot
    tell the two apart would report a machine it failed to examine exactly as
    it reports a machine with nothing to find.

    The walk stops at pid 0/1, at ``limit`` generations, or on a cycle -- pids
    are recycled, and a table read while processes are exiting can contain a
    loop that would otherwise spin here forever.
    """
    try:
        current = int(pid if pid is not None else os.getppid())
    except Exception:
        return None

    table: "dict[int, tuple[int, str]] | None"
    if IS_WINDOWS:
        table = _win_process_table()
        if table is None:
            return None
    elif _procfs_available():
        table = None  # walked one generation at a time below
    else:
        table = _ps_process_table()
        if table is None:
            return None

    chain: list[dict] = []
    seen: set[int] = set()
    try:
        while current and current > 0 and len(chain) < limit:
            if current in seen:
                break
            seen.add(current)

            if table is not None:
                entry = table.get(current)
                if entry is None:
                    break
                ppid, name = entry
                path = _win_image_path(current) if IS_WINDOWS else None
            else:
                got = _posix_parent(current)
                if got is None:
                    break
                ppid, name = got
                path = name or None

            chain.append({
                "pid": current,
                "name": os.path.basename(name) if name else None,
                "path": path,
            })
            current = ppid
    except _TreeUnreadable:
        # Whatever was gathered so far is true, but it is a prefix, and a
        # prefix is indistinguishable from a complete chain once returned.
        # The dangerous direction is the silent one: an incomplete chain with
        # no copilot ancestor in it reads as "launched by a human". Report
        # that we could not look rather than let a partial answer pass as a
        # whole one.
        return None

    # A table that was readable but produced nothing for our own parent is a
    # genuine dead end rather than a failure to look, so an empty chain here
    # is reported as an empty chain. It is still distinguishable from `None`.
    return chain


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    try:
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _append(path: Path, record: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def record_supervisor_start(operator_home: Path, *, instance: str,
                            session: int, code: "dict | None" = None) -> None:
    """Record that a loop supervisor came up, and on what code. Never raises.

    A supervisor imports the operator once and runs it for the whole run, so
    every record it later writes describes the code it started with, not the
    code on disk when the record was read. Without this event the two are
    indistinguishable: the fix that made ``session_exit`` report handoff
    endings landed at 19:36 on 2026-08-04 and every supervisor had started at
    13:28, so records written *after* the fix were still produced by
    instruments without it, and nothing in them said so.

    ``code`` is stamped rather than the mere version string, because the
    version only moves when deployed artifacts change -- that very fix bumped
    nothing, so a version field would have reported the stale supervisor and
    the fixed one as identical.
    """
    try:
        payload = {
            "ts": _utcnow(),
            "event": "supervisor_start",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
        }
        if isinstance(code, dict):
            payload["code"] = code.get("digest")
            payload["toolkit_version"] = code.get("version")
        _append(trace_path(Path(operator_home)), payload)
    except Exception:
        return


def record_session_exit(operator_home: Path, *, instance: str, session: int,
                        pid: "int | None", markers: "dict",
                        consecutive: int, limit: int,
                        code: "str | None" = None) -> None:
    """Record that a supervised copilot session ended. Never raises.

    This is the event the evidence was built for and the one an invocation log
    cannot see. When seven loops died together on 2026-08-03 no operator
    command was run at all -- each supervisor was already inside its poll
    loop, so there was nothing to attribute. ``operator.log`` said "copilot
    exited unexpectedly" seven times and could say nothing else, because the
    supervisor never waits on the child: it polls liveness, and a process that
    is gone leaves no exit code behind to read.

    So "unexpected" here means only *unexplained* -- no stop, detach or
    restart marker was set. It is not evidence of a crash, and the distinction
    matters: the copilot logs for that incident end with an orderly
    ``[shutdown] Shutdown complete``, and the extensions died with
    ``0xC000013A`` (``STATUS_CONTROL_C_EXIT``), which is a console control
    event delivered to every process sharing the console rather than a fault
    in any one session. What is recorded here is therefore the observation and
    the marker states it was judged against, so a later reader can re-judge
    it. Nothing here decides what killed the session.

    Endings that *were* explained are recorded too, and that is not a cosmetic
    addition: for a long time only the unexplained branch called this, so
    every record carried ``restart=False`` and the evidence could be read -- was
    read -- as proving no session had ever ended by handoff. A population that
    excludes the cases you are trying to count cannot answer the question, and
    it does not look empty while failing to.

    ``code`` fingerprints the operator source the *supervisor* is running, so
    a later reader can scope a re-measurement to records from an instrument
    that had a given fix. Scoping by date cannot do this: a supervisor keeps
    the code it imported at startup, so records dated after a fix are still
    written by supervisors without it.
    """
    try:
        _append(trace_path(Path(operator_home)), {
            "ts": _utcnow(),
            "event": "session_exit",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "session_pid": pid,
            # Tri-state per marker: True set, False absent, None unreadable.
            # An unreadable marker is why the supervisor waits instead of
            # relaunching, so flattening it here would hide the reason.
            "markers": dict(markers),
            "consecutive": consecutive,
            "limit": limit,
            "giving_up": consecutive >= limit,
            "code": code,
        })
    except Exception:
        return
