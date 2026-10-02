"""Parent chain of a pid.

Handoff matches the caller to the copilot process the runner recorded.
``None`` means the table could not be read. An empty list means it was read
and this pid has no parent we can vouch for.

On Windows the chain ends at the first parent that is gone, unreadable, or
born after its child, because ToolHelp keeps a dead parent's pid and Windows
reuses pids. What is returned is the part of the chain that was verified, so
a stop there can only make handoff refuse. The order test trusts process
creation times: two processes created in the same clock tick, or a clock set
backwards between a parent's death and its pid's reuse, defeat it. That is
acceptable for an identity check against confusion, which is all this is.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


IS_WINDOWS = os.name == "nt"

#: A recycled pid can put a loop in one snapshot. The cap is the other stop,
#: far above any copilot tree, so a loop cannot spin the caller.
_ANCESTRY_CAP = 64

_UNREADABLE = object()


def _win_parents() -> "dict[int, int] | None":
    """``{pid: ppid}`` from one ToolHelp snapshot, or ``None`` if it failed."""
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
        # restype defaults to c_int, which truncates a HANDLE on 64-bit
        # Windows and turns a failed snapshot into a handle we would walk.
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
            table: dict[int, int] = {}
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
            return table
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return None


def _linux_parent(pid: int):
    """ppid from ``/proc/<pid>/stat``, ``None`` if gone, unreadable otherwise."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8",
                                                   errors="replace")
    except FileNotFoundError:
        return None
    except OSError:
        return _UNREADABLE
    close = stat.rfind(")")
    if close < 0:
        return None
    fields = stat[close + 2:].split()
    # field 4 is ppid, the second field after comm.
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _ps_parent(pid: int):
    """ppid via ``ps -o ppid=``, for POSIX without ``/proc``."""
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid="],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return _UNREADABLE
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    if not text.isdigit():
        return _UNREADABLE
    return int(text)


def _procfs() -> bool:
    try:
        return Path("/proc").is_dir()
    except OSError:
        return False


def _win_created(pid: int) -> "int | None":
    """Creation time of a live process, or ``None`` if it is gone or closed to us."""
    import process_identity
    token = process_identity.process_start_token(pid)
    if not token or not token.startswith("win:"):
        return None
    try:
        return int(token[4:])
    except ValueError:
        return None


def ancestry(pid: int) -> "list[int] | None":
    """Parents of ``pid``, nearest first. ``None`` if the table cannot be read."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    table = None
    if IS_WINDOWS:
        table = _win_parents()
        if table is None:
            return None
        child_born = _win_created(pid)
    use_proc = not IS_WINDOWS and _procfs()

    chain: list[int] = []
    seen: set[int] = {pid}
    current = pid
    while len(chain) < _ANCESTRY_CAP:
        if IS_WINDOWS:
            parent = table.get(current)
            if parent is not None and parent > 0:
                # Windows keeps a dead parent's pid and reuses pids, so the
                # recorded parent may be a stranger born after the child.
                born = _win_created(parent)
                if born is None or child_born is None or born > child_born:
                    break
                child_born = born
        elif use_proc:
            parent = _linux_parent(current)
        else:
            parent = _ps_parent(current)
        if parent is _UNREADABLE:
            return None
        if parent is None or parent <= 0 or parent == current or parent in seen:
            break
        seen.add(parent)
        chain.append(parent)
        if parent == 1:
            break
        current = parent
    return chain
