"""What `operator` can do, as data the help text reads.

`entry.py` decides what to run. This names what exists, so a verb can be
typed and explained without a second list that can drift. The keyboard menu
does not read this table. Handoff is a verb for the agent, not a menu row.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verb:
    tokens: tuple[str, ...]
    help: str


VERBS: tuple[Verb, ...] = (
    Verb(("doctor",), "check that this machine can run operator"),
    Verb(("start",), "start a supervised operator (start [NAME] [TASK])"),
    Verb(("list",), "list operators"),
    Verb(("attach",), "attach this terminal to a running operator"),
    Verb(("stop",), "stop an operator and every operator it started"),
    Verb(("rename",), "rename an operator"),
    Verb(("delete",), "delete an operator and its settings"),
    Verb(("recover",), "list operators that need recovering after a crash"),
    Verb(("handoff",), "write this operator's handoff and start the next session"),
    Verb(("send",), "message an operator's parent or child (send NAME TEXT)"),
    Verb(("inbox",), "read the messages sent to you"),
)
