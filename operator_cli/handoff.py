"""`operator handoff` -- end this session and leave the next one a record.

The preamble has advertised this to every seat since it was written, naming a
bare `handoff` that belongs to `copilot-tools` rather than here. The kernel's
half was always present: `exits.handoff_state` reads the file, `supervisor.py`
polls the marker, and nothing in this distribution wrote either. Same shape as
`project.py` and `ext.py`: parse the flags, call in, report what happened.
"""
from __future__ import annotations

import sys
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from .fleet import _bootstrap

#: Everything that takes a value. `--no-restart` is deliberately absent: it is
#: a switch, and listing it here would make it swallow the token after it.
VALUE_FLAGS = ("--instance", "--status", "--next", "--context")
SWITCHES = ("--no-restart",)

USAGE = ("Usage: operator handoff --instance NAME --status TEXT "
         "[--next TEXT] [--context TEXT] [--no-restart]")


def parse(options: list[str]) -> "dict[str, str] | None":
    """Flag values, or None after saying which argument was the problem.

    Nothing is skipped in silence and nothing option-shaped is taken as text.
    A membership test was not enough: `--norestart` and `--no-restart=true`
    are not members and both ended the session they were typed to preserve.
    Use `--context=--no-restart` to mean such a string literally.
    """
    values: dict[str, str] = {}
    i = 0
    while i < len(options):
        arg = options[i]
        name, eq, inline = arg.partition("=")
        if name in VALUE_FLAGS:
            if eq:
                values[name] = inline
                i += 1
                continue
            following = options[i + 1] if i + 1 < len(options) else ""
            if i + 1 >= len(options) or following.startswith("-"):
                # A status may legitimately open with a dash, and telling that
                # user "needs a value" when they supplied one is a lie.
                hint = (f", and {following!r} looks like an option. To mean it "
                        f"literally, write {name}={following}"
                        if following else "")
                print(f"operator handoff {name} needs a value{hint}",
                      file=sys.stderr)
                return None
            values[name] = following
            i += 2
            continue
        if arg in SWITCHES:
            values[arg] = "yes"
            i += 1
            continue
        if arg.startswith("-"):
            print(f"operator handoff: unknown option {arg}", file=sys.stderr)
            return None
        if "--instance" not in values:
            # `operator handoff alpha --status ...`, the shape every other
            # verb here accepts for a seat name.
            values["--instance"] = arg
            i += 1
            continue
        print(f"operator handoff: unexpected argument {arg!r}", file=sys.stderr)
        return None
    return values


def main(rest: list[str]) -> int:
    _bootstrap()
    from exits import (CATALOG_UNREADABLE, WRITE_FAILED, request_restart,
                       write_handoff)
    from paths import guid_is_usable
    options, _ = at_dashdash(rest)
    if "-h" in options or "--help" in options:
        print(USAGE)
        return 0
    values = parse(options)
    if values is None:
        return 2
    seat = values.get("--instance", "").strip()
    status = values.get("--status", "").strip()
    if not seat or not status:
        print(USAGE, file=sys.stderr)
        return 2
    if not guid_is_usable(seat):
        # The same refusal `operator-seat` makes, by the same predicate. A
        # seat name is one component of a filename the next session has to
        # find, so `../elsewhere` is not a seat this can write for.
        print(f"the seat name {seat!r} is not usable", file=sys.stderr)
        return 2
    landed = write_handoff(Path.cwd(), seat, status,
                           values.get("--next", ""),
                           values.get("--context", ""))
    if landed is CATALOG_UNREADABLE:
        print("could not read the project catalog", file=sys.stderr)
        return 1
    if landed is None:
        print("this directory is not a registered project", file=sys.stderr)
        print("register it with: operator project register", file=sys.stderr)
        return 1
    if landed is WRITE_FAILED:
        print("could not write the handoff", file=sys.stderr)
        return 1
    print(f"handoff written to {landed}")
    if "--no-restart" in values:
        # The write is the durable half and the restart is not always wanted.
        # A seat checkpointing before a long step needs the file on disk and
        # needs to keep running.
        return 0
    if not request_restart(seat):
        print("the handoff was written but the restart could not be requested",
              file=sys.stderr)
        return 1
    print(f"restart requested for {seat}")
    return 0
