"""The process chain above a pid.

A later check matches a caller to the Copilot process a supervisor recorded.
``_own_instance_id`` already uses the same walk. ``None`` means the table
could not be read. An empty list means it was read and this process has no
ancestors, which is a different fact.
"""
from __future__ import annotations

import os
from pathlib import Path


IS_WINDOWS = os.name == "nt"


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
