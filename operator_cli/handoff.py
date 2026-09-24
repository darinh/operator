"""`operator handoff` -- end this session and leave the next one a record.

The launch preamble has told every seat to run this since the preamble was
written, and until now it named a bare `handoff`: a console script of
`copilot-tools`, the predecessor this project is designed not to assume is
installed. The kernel's half was always here. `exits.handoff_state` reads the
file, `supervisor.py` polls the marker, and nothing in this distribution wrote
either one.

A module of its own rather than another branch in `entry.py`, which was four
code lines under its ceiling when this arrived. That ceiling is the repository
asking for a seam instead of a bigger number, and `project.py`, `ext.py` and
`seat.py` are the same shape: parse the flags, call the kernel, print what
happened.
"""
from __future__ import annotations

import sys
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from .fleet import _bootstrap

#: Everything that takes a value. `--no-restart` is deliberately absent: it is
#: a switch, and listing it here would make it swallow the token after it.
VALUE_FLAGS = ("--instance", "--status", "--next", "--context")

USAGE = ("Usage: operator handoff --instance NAME --status TEXT "
         "[--next TEXT] [--context TEXT] [--no-restart]")


def _parse(options: list[str]) -> "dict[str, str] | None":
    """Flag values, or None after printing which flag was left dangling."""
    values: dict[str, str] = {}
    i = 0
    while i < len(options):
        arg = options[i]
        if arg in VALUE_FLAGS:
            if i + 1 >= len(options):
                print(f"operator handoff {arg} needs a value", file=sys.stderr)
                return None
            values[arg] = options[i + 1]
            i += 2
            continue
        if not arg.startswith("-") and "--instance" not in values:
            # `operator handoff alpha --status ...`, the shape every other verb
            # here accepts for a seat name.
            values["--instance"] = arg
        i += 1
    return values


def main(rest: list[str]) -> int:
    _bootstrap()
    from exits import CATALOG_UNREADABLE, request_restart, write_handoff
    options, _ = at_dashdash(rest)
    if "-h" in options or "--help" in options:
        print(USAGE)
        return 0
    values = _parse(options)
    if values is None:
        return 2
    seat = values.get("--instance", "")
    if not seat or not values.get("--status"):
        print(USAGE, file=sys.stderr)
        return 2
    landed = write_handoff(Path.cwd(), seat, values["--status"],
                           values.get("--next", ""),
                           values.get("--context", ""))
    if landed is CATALOG_UNREADABLE:
        print("could not read the project catalog", file=sys.stderr)
        return 1
    if landed is None:
        print("this directory is not a registered project", file=sys.stderr)
        print("register it with: operator project register", file=sys.stderr)
        return 1
    print(f"handoff written to {landed}")
    if "--no-restart" in options:
        # The write is the durable half and the restart is not always wanted.
        # A seat checkpointing before a long step needs the file on disk and
        # needs to keep running.
        return 0
    request_restart(seat)
    print(f"restart requested for {seat}")
    return 0
