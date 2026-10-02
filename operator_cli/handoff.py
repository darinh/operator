"""`operator handoff` -- end this session and leave the next one a record.

The preamble has advertised this to every operator since it was written, naming a
bare `handoff` that belongs to `copilot-tools` rather than here. The kernel's
half was always present: `exits.handoff_state` reads the file and
`supervisor.py` polls the marker, while nothing here wrote either.
"""
from __future__ import annotations

import sys
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from .home import _bootstrap

#: Takes a value. A switch listed here would swallow the token after it.
VALUE_FLAGS = ("--instance", "--status", "--next", "--context")
SWITCHES = ("--no-restart",)

USAGE = ("Usage: operator handoff --instance ID --status TEXT "
         "[--next TEXT] [--context TEXT] [--no-restart]")


def parse(options: list[str]) -> "dict[str, str] | None":
    """Flag values, or None after saying which argument was the problem.

    Nothing is skipped in silence and nothing option-shaped is taken as text.
    A membership test was not enough: `--norestart` and `--no-restart=true`
    are not members and both ended the session they were typed to preserve.
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
                # A value may legitimately open with a dash, and "needs a
                # value" is a lie to a user who supplied one. The shape is
                # described rather than printed as a literal to paste: no
                # quoting is correct for every shell once the value carries
                # quotes of its own, and two review rounds went on proving it.
                hint = (f", and {following!r} looks like an option. To mean it "
                        f"literally, pass it as one argument starting {name}=, "
                        f"quoted for your shell" if following else "")
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
            # verb here accepts for an operator name.
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
    operator = values.get("--instance", "").strip()
    status = values.get("--status", "").strip()
    if not operator or not status:
        print(USAGE, file=sys.stderr)
        return 2
    if not guid_is_usable(operator):
        # An operator id is one component of a filename the next session has to
        # find, so `../elsewhere` is not an id this can write for.
        print(f"the operator id {operator!r} is not usable", file=sys.stderr)
        return 2
    landed = write_handoff(Path.cwd(), operator, status,
                           values.get("--next", ""),
                           values.get("--context", ""))
    if landed is CATALOG_UNREADABLE:
        print("could not read the project catalog", file=sys.stderr)
        return 1
    if landed is None:
        print("this directory is not a registered project", file=sys.stderr)
        print("start an operator in this directory first", file=sys.stderr)
        return 1
    if landed is WRITE_FAILED:
        print("could not write the handoff", file=sys.stderr)
        return 1
    print(f"handoff written to {landed}")
    if "--no-restart" in values:
        # The write is the durable half and the restart is not always wanted.
        # An operator checkpointing before a long step needs the file on disk and
        # needs to keep running.
        return 0
    if not request_restart(operator):
        print("the handoff was written but the restart could not be requested",
              file=sys.stderr)
        return 1
    print(f"restart requested for {operator}")
    return 0
