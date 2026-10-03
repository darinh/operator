"""TTY detection for the front door."""
from __future__ import annotations


def isatty(stream) -> bool:
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (ValueError, OSError):
        return False
