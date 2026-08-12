"""Process identity, because a pid is not one.

Extracted from a 21-definition liveness module; the kernel uses six. A
recycled pid reads as a live process, so identity is pid plus a start
token, and on Linux the token is boot-relative -- hence `same_boot`, which
answers "cannot tell" across kinds rather than guessing.
"""
from __future__ import annotations
import ctypes
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from mux import Mux                                   # noqa: E402
from claims import parse_ts                               # noqa: E402


#: How far apart two *computed* boot instants may be and still be one boot.
#:
#: Only platforms whose boot identity is a timestamp are affected. Linux hands
#: out a kernel-generated uuid, which is exact and compares exactly; Windows
#: and macOS report an instant, and an instant moves -- the kernel adjusts its
#: recorded boot time when the wall clock is corrected, and the tick-count
#: fallback drifts a little on its own.
#:
#: Two minutes, and the direction of the error is the point. Too *wide* costs
#: a conclusive DEAD that the mux and pid probes then have to reach instead;
#: too *narrow* reports a live agent as dead because NTP nudged the clock. The
#: first is a slower answer, the second is two agents in one worktree.
BOOT_INSTANT_TOLERANCE = 120


_LINUX_BOOT_ID = "/proc/sys/kernel/random/boot_id"


#: 100-nanosecond intervals between 1601-01-01 (the Windows epoch) and
#: 1970-01-01 (the Unix one).
_FILETIME_EPOCH_DELTA = 116_444_736_000_000_000


_STILL_ACTIVE = 259


#: The weakest access right that answers "does this pid exist, and when did it
#: start", so the probe succeeds for processes we may not open fully.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


#: The widest pid either platform can represent: a ``DWORD`` on Windows, an
#: ``int32`` ``pid_t`` on POSIX. Anything larger is not a big pid, it is a
#: number that ``ctypes`` will quietly truncate into somebody else's pid.
_PID_MAX = 0xFFFFFFFF if platform.system() == "Windows" else 0x7FFFFFFF


IS_WINDOWS = platform.system() == "Windows"


# ── boot identity ───────────────────────────────────────────────
def _linux_boot_uuid() -> "str | None":
    try:
        raw = Path(_LINUX_BOOT_ID).read_bytes()
    except OSError:
        return None
    token = raw.decode("utf-8", "replace").strip()
    return token or None


def _windows_boot_instant() -> "int | None":
    """Unix seconds at which this Windows box booted, or ``None``.

    ``NtQuerySystemInformation(SystemTimeOfDayInformation)`` first, because it
    reports the boot time the kernel *recorded* rather than one derived from
    an uptime counter. The ``GetTickCount64`` fallback subtracts uptime from
    now, which is correct to within the timer's drift -- covered by
    :data:`BOOT_INSTANT_TOLERANCE`, which is why the fallback is allowed to
    exist at all rather than reporting "cannot tell".
    """
    class _TimeOfDay(ctypes.Structure):
        _fields_ = [
            ("BootTime", ctypes.c_longlong),
            ("CurrentTime", ctypes.c_longlong),
            ("TimeZoneBias", ctypes.c_longlong),
            ("TimeZoneId", ctypes.c_ulong),
            ("Reserved", ctypes.c_ulong),
            ("BootTimeBias", ctypes.c_ulonglong),
            ("SleepTimeBias", ctypes.c_ulonglong),
        ]

    try:
        ntdll = ctypes.WinDLL("ntdll")
        info = _TimeOfDay()
        returned = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(
            3, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
        if status == 0 and info.BootTime > 0:
            return int((info.BootTime - _FILETIME_EPOCH_DELTA) // 10_000_000)
    except Exception:
        pass
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        uptime = kernel32.GetTickCount64() / 1000.0
    except Exception:
        return None
    return int(time.time() - uptime)


def _sysctl_boot_instant() -> "int | None":
    """Unix seconds at which this macOS/BSD box booted, or ``None``.

    ``kern.boottime`` prints ``{ sec = 1690000000, usec = 12345 } ...``; only
    the seconds are read, and a line that does not carry them is reported as
    unknown rather than parsed optimistically.
    """
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    marker = "sec ="
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1].strip()
    digits = ""
    for char in tail:
        if not char.isdigit():
            break
        digits += char
    if not digits:
        return None
    return int(digits)


def boot_identity() -> "str | None":
    """This machine's identity for the current boot, or ``None``.

    Two shapes, tagged so they can never be compared across kinds by accident:

    * ``uuid:<token>`` -- Linux's kernel-generated boot id. Exact.
    * ``instant:<unix seconds>`` -- Windows and macOS. Compared with
      :data:`BOOT_INSTANT_TOLERANCE`, because the value moves.

    The tag is not decoration. A claim written on one platform and read on
    another -- or on a machine whose exact source stopped answering -- must
    compare as *unknown*, not as a mismatch, and an untagged string could only
    do the latter.
    """
    if IS_WINDOWS:
        instant = _windows_boot_instant()
        return None if instant is None else f"instant:{instant}"
    token = _linux_boot_uuid()
    if token:
        return f"uuid:{token}"
    instant = _sysctl_boot_instant()
    return None if instant is None else f"instant:{instant}"


def same_boot(recorded: "str | None", current: "str | None") -> "bool | None":
    """Whether two boot identities name the same boot. ``None`` if unknowable.

    Different kinds are unknowable, not different. So is either side being
    missing or unparseable: the caller must not read "we have no boot id for
    this claim" as "this claim is from another boot".
    """
    if not recorded or not current:
        return None
    kind, _, left = str(recorded).partition(":")
    other, _, right = str(current).partition(":")
    if not left or not right or kind != other:
        return None
    if kind == "uuid":
        return left == right
    if kind == "instant":
        try:
            return abs(int(left) - int(right)) <= BOOT_INSTANT_TOLERANCE
        except ValueError:
            return None
    return None


# ── process identity ────────────────────────────────────────────
def _win_open(pid: int):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int,
                                     ctypes.c_ulong]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  int(pid))
    return kernel32, handle


def _win_start_token(pid: int) -> "str | None":
    try:
        kernel32, handle = _win_open(pid)
    except Exception:
        return None
    if not handle:
        return None
    try:
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_ulong)]
        code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        if code.value != _STILL_ACTIVE:
            # An exited process stays openable for as long as somebody holds a
            # handle to it, and it keeps its creation time. Handing that back
            # would name a run that has ended -- and this token exists to tell
            # one run of a pid from the next, so a token for a finished run is
            # the exact ambiguity it is supposed to remove.
            return None
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong)]
        created = ctypes.c_ulonglong(0)
        exited = ctypes.c_ulonglong(0)
        kernel = ctypes.c_ulonglong(0)
        user = ctypes.c_ulonglong(0)
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user))
        if not ok or not created.value:
            return None
        return f"win:{created.value}"
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _linux_start_token(pid: int) -> "str | None":
    """Field 22 of ``/proc/<pid>/stat`` -- start time in clock ticks since boot.

    Opaque on purpose. Nothing here needs to know *when* the process started,
    only whether it is the same process as the one that wrote the claim, and
    equality of the raw field answers that without a unit conversion that could
    be wrong on a kernel with a different ``HZ``.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
    except OSError:
        return None
    # The comm field is parenthesised and may itself contain spaces and
    # parentheses, so the fields after it are found from the LAST ')'.
    close = stat.rfind(")")
    if close == -1:
        return None
    fields = stat[close + 2:].split()
    # stat's field 22 is the 20th after comm, and the split above drops the
    # first two fields (pid and comm) -- so index 19 counting from 'state'.
    if len(fields) < 20:
        return None
    return f"linux:{fields[19]}"


def _ps_start_token(pid: int) -> "str | None":
    """``ps -o lstart=`` for macOS and BSD, kept as the raw string.

    Not parsed: the format is locale-dependent, and every parse of it is a
    chance to turn one process into another. Two reads of the same process
    give byte-identical strings, and that is the entire requirement.

    Which is why the *rendering* is pinned rather than inherited. ``lstart``
    is a wall-clock date printed through ``LC_TIME`` and ``TZ``, so the same
    process read from two shells with different settings yields two different
    strings -- and since `copilot_operator._loop_pid_reused` spends a token
    mismatch on deleting a supervisor's pid file, and `assess` spends one on
    declaring a claim's owner DEAD, an inherited environment would let a
    perfectly live process be disowned by whichever command happened to run
    under the other locale. ``LC_ALL``/``LC_TIME``/``TZ`` make the token a
    property of the process instead of of the caller.

    Tagged ``psc:`` rather than ``ps:`` for the same reason `boot_identity`
    tags its two shapes: the pin *changes the string* for a process that has
    not moved, and every reader of a pre-pin ``ps:`` token compares for
    equality. Untagged, a claim recorded before the upgrade would compare
    unequal to its own live owner and `assess` would return DEAD -- which is
    reclaimable, so a live agent's worktree could be handed to somebody else.
    Tagged, `same_start_token` answers "cannot tell" across the two kinds and
    the migration costs a probe's worth of evidence instead of a session.
    Found by adversarial review.
    """
    try:
        proc = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "lstart="],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
            env={**os.environ, "LC_ALL": "C", "LC_TIME": "C", "LANG": "C",
                 "TZ": "UTC"},
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return f"psc:{token}" if token else None


def _coerce_pid(pid) -> "int | None":
    """``pid`` as a positive integer, or ``None`` if it is not one.

    Deliberately not ``int(pid)``. ``int(1.5)`` is ``1``, so a float pid would
    not be *refused*, it would be silently answered about a different, real
    process -- and the caller would be told, with confidence, whether init was
    running. ``bool`` is an ``int`` subclass in Python and ``int(True)`` is
    likewise pid 1, so it is refused by name.

    Zero and negatives are refused for the same reason rather than as
    tidiness: on POSIX they are signal-group selectors, and ``os.kill(0, 0)``
    probes *our own* process group -- an answer about something nobody asked
    that would always come back True.

    Values above :data:`_PID_MAX` are refused for the third time for the same
    reason. A pid is a 32-bit ``DWORD`` on Windows and a 32-bit ``pid_t`` on
    POSIX, and ``ctypes`` truncates rather than complains: ``OpenProcess``
    handed ``(1 << 32) + os.getpid()`` opens *this* process and reports it
    running. Refusing is the only answer that is not about some other process.
    """
    if pid is None or isinstance(pid, bool):
        return None
    if isinstance(pid, int):
        value = pid
    elif isinstance(pid, str):
        text = pid.strip()
        if not text.isdigit():
            return None
        value = int(text)
    else:
        return None
    if value <= 0 or value > _PID_MAX:
        return None
    return value


#: The kinds of start token `process_start_token` can return, as the tag
#: before the first ``:``. Named here rather than at the readers, so a fourth
#: producer cannot be added without the classifiers above and below seeing it.
#:
#: ``ps`` is read but never written any more: it is the pre-pin macOS/BSD
#: rendering, made through whatever ``LC_TIME`` and ``TZ`` the caller
#: happened to have. Tokens recorded under it are still real evidence about
#: the process that wrote them -- they simply cannot be compared with a
#: ``psc`` one, which is what `same_start_token` is for.
START_TOKEN_KINDS = ("win", "linux", "ps", "psc")


#: Kinds whose value is a machine number, so a damaged one is recognisable.
#: ``ps``/``psc`` carry a date string and cannot be checked this way.
_NUMERIC_TOKEN_KINDS = ("win", "linux")


def is_start_token(token: "str | None") -> bool:
    """Is this a value `process_start_token` could actually have produced?

    A reader that spends a token mismatch on something destructive needs to
    know the difference between "a token that names a different run" and "a
    damaged field", because only the first is evidence. Every token this
    module produces is ``kind:value`` with a non-empty value and a kind from
    :data:`START_TOKEN_KINDS`, and the two numeric kinds carry digits.

    Not a substitute for writing the file atomically: a *truncated* token is
    still well-formed by this test -- ``win:13430`` is a prefix of
    ``win:134308020110986193`` and both are digits. It rules out values that
    never came from here at all, which is the corruption a reader can
    recognise on its own.
    """
    if not isinstance(token, str):
        return False
    kind, sep, value = token.partition(":")
    if not sep or kind not in START_TOKEN_KINDS or not value:
        return False
    if kind in _NUMERIC_TOKEN_KINDS:
        return value.isdigit()
    return True


def same_start_token(recorded: "str | None", live: "str | None") -> "bool | None":
    """Do two start tokens name the same run of a pid? ``None`` if unknowable.

    Equality, with the same tri-state discipline `same_boot` uses and for the
    same reason: the caller must not read "these were rendered differently"
    as "this is a different process". Either side missing, either side
    damaged, or the two carrying different kinds is ``None``.

    Different kinds is the case that matters. ``psc`` replaced ``ps`` when the
    macOS/BSD probe pinned its locale and timezone, so a token recorded before
    that change describes the same process in another rendering -- and every
    reader here compares tokens for equality to decide something destructive.
    `assess` would have returned DEAD for a live claim owner, which is
    reclaimable, so an agent still working could have had its worktree handed
    to somebody else; `copilot_operator._loop_pid_reused` would have deleted a
    running supervisor's pid file. Neither has any evidence to act on: the two
    strings were never comparable.
    """
    if not is_start_token(recorded) or not is_start_token(live):
        return None
    if recorded.partition(":")[0] != live.partition(":")[0]:
        return None
    return recorded == live


def start_token_is_boot_relative(token: "str | None") -> bool:
    """Is this start token only meaningful within the boot that produced it?

    ``_linux_start_token`` is field 22 of ``/proc/<pid>/stat`` -- clock ticks
    **since boot** -- so two processes from different boots can carry the same
    token with no relationship to each other. The other two shapes are
    absolute instants: ``win:`` is a FILETIME and ``ps:`` is a wall-clock
    date, and neither can collide across a reboot.

    Lives here rather than at the call sites because the token formats are
    this module's, and a reader elsewhere testing for ``"linux:"`` would be a
    copy of that knowledge that no change to the format could reach. Callers
    use it to decide whether the extra `boot_identity` probe buys anything --
    on macOS that probe forks ``sysctl``, so asking for it where it cannot
    discriminate is a subprocess per call for nothing.

    An unreadable or absent token is *not* boot-relative: there is nothing for
    the boot identity to qualify, so the caller has no comparison to make.
    """
    return isinstance(token, str) and token.startswith("linux:")


def process_start_token(pid: "int | None") -> "str | None":
    """An opaque token identifying *this* run of ``pid``, or ``None``.

    Compared only for equality, and only against a token recorded for the same
    pid. That is what makes the pid probe safe across reuse: a recycled pid
    carries a different start time, so a claim whose owner died and whose pid
    was handed to something else reads as DEAD rather than as still running.
    """
    pid = _coerce_pid(pid)
    if pid is None:
        return None
    if IS_WINDOWS:
        return _win_start_token(pid)
    token = _linux_start_token(pid)
    if token:
        return token
    return _ps_start_token(pid)
