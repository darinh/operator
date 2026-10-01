"""What `operator` can do, as data rather than as branches.

Split out of `entry.py`, which was at exactly 500 of its 500 allowed code
lines when a new verb arrived. The ceiling in `test_kernel_boundary.py` is the
repository asking for a seam rather than a bigger number, and the table is the
part of that file which was never routing: `entry.py` decides what to run,
this decides what exists. One table drives three surfaces -- the help text,
the numbered menu, and the argv the menu builds -- so a verb that is typeable
but unreachable from the menu, or listed but unrunnable, is not expressible.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verb:
    tokens: tuple[str, ...]
    help: str
    menu: str
    prompts: tuple[str, ...] = ()
    extra_menu: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Item:
    label: str
    argv: tuple[str, ...]
    prompts: tuple[str, ...] = ()


VERBS: tuple[Verb, ...] = (
    Verb(("doctor",), "check that this machine can run operator", "Check this machine"),
    Verb(("start",), "start a supervised seat (start --name NAME)", "Start a supervised seat"),
    Verb(("list",), "list running seats", "List running seats"),
    Verb(("join",), "attach this terminal to a running seat", "Join a running seat", ("name",)),
    Verb(("stop",), "ask a seat's supervisor to stop", "Stop a supervised seat", ("name",)),
    Verb(("recover",), "list seats that need recovering after a crash", "List seats that need recovering", extra_menu=(("Recover every seat that needs it", ("recover", "--all")),)),
    Verb(("handoff",), "write this seat's handoff and start the next session",
         "Hand off to the next session", ("instance", "status")),
)


PROMPT_LABEL = {
    "name": ("Seat name: ", "seat name"),
    "instance": ("Seat name: ", "seat name"),
    "status": ("What you completed: ", "status"),
}

#: Prompt keys the CLI passes as `--key value` rather than positionally.
FLAGGED = ("instance", "status")


def menu_items() -> tuple[Item, ...]:
    items = []
    for verb in VERBS:
        items.append(Item(verb.menu, verb.tokens, verb.prompts))
        for label, argv in verb.extra_menu:
            items.append(Item(label, argv))
    return tuple(items)


def build_argv(item: Item, values: dict[str, str]) -> list[str]:
    """The command the menu will run, and print before running it.

    `instance` goes in front of the verb's own tokens because `operator-seat`
    declares it on the top-level parser; the rest follow the verb.
    """
    argv = list(item.argv)
    if "instance" in values:
        argv = [argv[0], "--instance", values["instance"], *argv[1:]]
    for key in FLAGGED[1:]:
        if key in values:
            argv += [f"--{key}", values[key]]
    for key in ("name",):
        if key in values:
            argv += [values[key]]
    return argv
