"""What `operator` can do, as data rather than as branches.

Split out of `entry.py`, which was at exactly 500 of its 500 allowed code
lines when a new verb arrived. The ceiling in `test_kernel_boundary.py` is the
repository asking for a seam rather than a bigger number, and the table is the
part of that file which was never routing: `entry.py` decides what to run,
this decides what exists.

One table drives three surfaces -- the help text, the numbered menu, and the
argv the menu builds -- so a verb that is typeable but unreachable from the
menu, or listed but unrunnable, is not expressible here.
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
    Verb(("restart-loop",), "replace a supervisor without stopping the session",
         "Restart one seat's supervisor", ("name",), extra_menu=(("Restart every running supervisor", ("restart-loop", "--all")),)),
    Verb(("recover",), "list seats that need recovering after a crash", "List seats that need recovering", extra_menu=(("Recover every seat that needs it", ("recover", "--all")),)),
    Verb(("handoff",), "write this seat's handoff and start the next session",
         "Hand off to the next session", ("instance", "status")),
    Verb(("project", "register"), "register this directory as a project", "Register this directory as a project"),
    Verb(("project", "list"), "list registered projects", "List registered projects"),
    Verb(("project", "forget"), "remove a registration, keep the journal", "Forget a project registration", ("path",)),
    Verb(("ext", "list"), "list registered extensions", "List extensions"),
    Verb(("ext", "enable"), "enable an extension", "Enable an extension", ("extension",)),
    Verb(("ext", "disable"), "disable an extension", "Disable an extension", ("extension",)),
    Verb(("remember",), "record one claim for this seat", "Remember something about this seat", ("instance", "kind", "text")),
    Verb(("recall",), "show what earlier sessions recorded", "Recall what this seat recorded", ("instance",)),
    Verb(("forget",), "stop recalling one journal entry", "Forget one journal entry", ("instance", "id")),
    Verb(("fleet", "run"), "poll the ledger and ask the extensions", "Run the fleet host"),
    Verb(("fleet", "proposals"), "show what is waiting for a human", "Show fleet proposals"),
    Verb(("trace",), "show recent ledger records, newest first", "Show recent ledger records"),
    Verb(("verify",), "check the ledger chain", "Verify the ledger chain"),
)

PROMPT_LABEL = {
    "name": ("Seat name: ", "seat name"),
    "instance": ("Seat name: ", "seat name"),
    "kind": ("Kind (decision, gotcha, disposition, attempt): ", "kind"),
    "text": ("Text: ", "note"),
    "id": ("Entry id: ", "entry id"),
    "path": ("Project directory: ", "directory"),
    "status": ("What you completed: ", "status"),
    "extension": ("Extension name: ", "extension"),
}

#: Prompt keys the CLI passes as `--key value` rather than positionally.
FLAGGED = ("instance", "kind", "status")


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
    for key in ("name", "text", "id", "path", "extension"):
        if key in values:
            argv += [values[key]]
    return argv
