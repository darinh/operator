"""Argv helpers for the front door: `--`, TTY, and paste-safe quoting."""
from __future__ import annotations

import os


def at_dashdash(argv: list[str]) -> tuple[list[str], list[str]]:
    try:
        i = argv.index("--")
    except ValueError:
        return argv, []
    return argv[:i], argv[i:]


def isatty(stream) -> bool:
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (ValueError, OSError):
        return False


def quote_one(arg: str) -> str:
    if os.name == "nt":
        if arg and all(ch.isalnum() or ch in ".-_/" for ch in arg):
            return arg
        return "'" + arg.replace("'", "''") + "'"
    import shlex
    return shlex.quote(arg)


def quote_argv(argv: list[str]) -> str:
    return " ".join(quote_one(a) for a in argv)
