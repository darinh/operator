"""Raw keystrokes, decoded without a terminal.

``decode`` turns a pull of one character at a time into key names. The
readers below are the only place that touches the console, and they always
put it back.
"""
from __future__ import annotations

import codecs
import os
import sys
from contextlib import contextmanager

#: How long a POSIX Esc may wait for the rest of an arrow sequence.
ESC_WAIT = 0.05
#: What ``_read_utf8`` returns for bytes that are not UTF-8. Never a key.
UNDECODABLE = "\ufffd"
#: The escape sequences that mean something here. Every other one is dropped whole.
_SEQUENCES = {"[A": "up", "[B": "down", "OA": "up", "OB": "down"}
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
    if len(ch) == 1 and ch != UNDECODABLE and ch.isprintable():
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
    # CSI carries parameter bytes before its final byte (Delete is ESC [ 3 ~,
    # Ctrl-Up is ESC [ 1 ; 5 A). Read through the final byte so no part of
    # an unknown sequence comes back as typed text. SS3 is one byte.
    body = ""
    while len(body) < 16 and ready(ESC_WAIT):
        nxt = take()
        body += nxt
        if second == "O" or not "\x20" <= nxt <= "\x3f":
            break
    if not body:
        return "esc"
    return _SEQUENCES.get(second + body, "")


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


@contextmanager
def cbreak(fd, termios, tty):
    """cbreak inside the block, the previous settings after it, however it ends."""
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
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
    with cbreak(fd, termios, tty):
        yield _posix_keys(fd)


def _windows_keys():
    import msvcrt

    def pull():
        return msvcrt.getwch() or ""

    yield from decode(pull, windows=True)


def _posix_keys(fd):
    import select

    held = bytearray()

    def pull():
        return _read_utf8(fd, held)

    def ready(timeout):
        return bool(select.select([fd], [], [], timeout)[0])

    yield from decode(pull, windows=False, ready=ready)


def _read_utf8(fd, held: bytearray) -> str:
    """One character, or ``UNDECODABLE`` for bytes that are not one.

    A byte that breaks a multibyte character is put back in ``held``, because
    it may be the start of the next real key.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    started = False
    while True:
        data = bytes([held.pop(0)]) if held else os.read(fd, 1)
        if not data:
            return ""
        try:
            text = decoder.decode(data)
        except UnicodeDecodeError:
            if started:
                held.insert(0, data[0])
            return UNDECODABLE
        started = True
        if text:
            return text
