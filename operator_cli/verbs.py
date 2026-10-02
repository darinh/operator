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
    Verb(("start",), "start a supervised operator (start [NAME])", "Start a supervised operator"),
    Verb(("list",), "list operators", "List operators"),
    Verb(("attach",), "attach this terminal to a running operator", "Attach to a running operator", ("name",)),
    Verb(("stop",), "ask an operator's supervisor to stop", "Stop a supervised operator", ("name",)),
    Verb(("rename",), "rename an operator", "Rename an operator", ("name", "new_name")),
    Verb(("delete",), "delete an operator and its settings", "Delete an operator", ("name",)),
    Verb(("recover",), "list operators that need recovering after a crash", "List operators that need recovering", extra_menu=(("Recover every operator that needs it", ("recover", "--all")),)),
    Verb(("handoff",), "write this operator's handoff and start the next session",
         "Hand off to the next session", ("instance", "status")),
)


PROMPT_LABEL = {
    "name": ("Operator name: ", "operator name"),
    "new_name": ("New name: ", "new name"),
    "instance": ("Operator name: ", "operator name"),
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

    ``instance`` is inserted after the verb as ``--instance``. Other flagged
    keys follow. ``name`` is appended as a positional.
    """
    argv = list(item.argv)
    if "instance" in values:
        argv = [argv[0], "--instance", values["instance"], *argv[1:]]
    for key in FLAGGED[1:]:
        if key in values:
            argv += [f"--{key}", values[key]]
    for key in ("name", "new_name"):
        if key in values:
            argv += [values[key]]
    return argv
