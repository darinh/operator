"""Raw keystrokes, decoded without a terminal.

``decode`` turns a pull of one character at a time into key names. The
readers below are the only place that touches the console, and they always
put it back.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager

#: How long a POSIX Esc may wait for the rest of an arrow sequence.
ESC_WAIT = 0.05
#: ENABLE_VIRTUAL_TERMINAL_PROCESSING. Output only. Input stays scan codes.
_VT = 0x0004


def decode(pull, *, windows: bool, ready=None):
    """Yield key names from ``pull() -> str``.

    ``""`` ends the stream. ``ready(seconds)`` says whether the next
    character is already waiting. POSIX uses it to tell a bare Esc from an
    arrow. Windows scan codes do not need it. ``\\x03`` raises
    ``KeyboardInterrupt`` on both.
    """
    pending = []

    def take():
        if pending:
            return pending.pop()
        return pull()

    while True:
        ch = take()
        if not ch:
            return
        if ch == "\x03":
            raise KeyboardInterrupt
        key = _windows(ch, take) if windows else _posix(ch, take, ready, pending)
        if key:
            yield key


def _named(ch: str) -> str:
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x1b":
        return "esc"
    if ch in ("\x08", "\x7f"):
        return "backspace"
    if ch == " ":
        return "space"
    if len(ch) == 1 and ch.isprintable():
        return ch
    return ""


def _windows(ch: str, take) -> str:
    if ch in ("\x00", "\xe0"):
        nxt = take()
        if nxt == "H":
            return "up"
        if nxt == "P":
            return "down"
        return ""
    return _named(ch)


def _posix(ch: str, take, ready, pending: list) -> str:
    if ch != "\x1b":
        return _named(ch)
    if ready is None or not ready(ESC_WAIT):
        return "esc"
    second = take()
    if second not in ("[", "O"):
        if second:
            pending.append(second)
        return "esc"
    if not ready(ESC_WAIT):
        return "esc"
    third = take()
    if third == "A":
        return "up"
    if third == "B":
        return "down"
    return ""


def enable_vt():
    """Turn on VT output. ``None`` when the console refuses, which is fine."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        if not kernel.SetConsoleMode(handle, mode.value | _VT):
            return None
        return (handle, mode.value)
    except (AttributeError, OSError):
        return None


def restore_vt(previous) -> None:
    if not previous:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(previous[0], previous[1])
    except (AttributeError, OSError):
        return


def guard_termios(fd, termios, tty, body):
    """cbreak for ``body()``, then the previous settings, including on interrupt."""
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return body()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@contextmanager
def raw_keys():
    """Yield key names. The terminal mode is restored on the way out."""
    if os.name == "nt":
        previous = enable_vt()
        try:
            yield _windows_keys()
        finally:
            restore_vt(previous)
        return
    import termios
    import tty
    fd = sys.stdin.fileno()
    # Same restore as guard_termios. The yield has to sit inside the try,
    # because the caller pulls keys after raw_keys returns the generator.
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield _posix_keys(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _windows_keys():
    import msvcrt

    def pull():
        return msvcrt.getwch() or ""

    yield from decode(pull, windows=True)


def _posix_keys(fd):
    import select

    def pull():
        return _read_utf8(fd)

    def ready(timeout):
        return bool(select.select([fd], [], [], timeout)[0])

    yield from decode(pull, windows=False, ready=ready)


def _read_utf8(fd) -> str:
    data = os.read(fd, 1)
    if not data:
        return ""
    while True:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            more = os.read(fd, 1)
            if not more:
                return ""
            data += more
