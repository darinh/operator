"""The launch preamble: how this session ends, and what the last one left."""
from __future__ import annotations

from instance import Instance


def build_preamble(instance: Instance, *, crash_recovery: bool = False,
                   handoff_waiting: str = "",
                   handoff_unknown: bool = False,
                   handoff_written: str = "") -> str:
    """What a session is told. Mechanism only.

    When to hand off is the repository's business. This says that a handoff
    ends the session, and where a waiting one is, and nothing about timing.
    """
    lines = [
        "You are running unattended under the operator supervisor, which "
        "relaunches this session when it ends. Nobody is reading this session.",
        "To end this session and start the next one, run: "
        '`operator handoff --status "..." --next "..."`.',
    ]
    if handoff_waiting:
        lines.append(
            "A handoff from the previous session is waiting for you. Read it "
            "before doing anything else. "
            f"It is at {handoff_waiting}."
        )
        if handoff_written:
            lines.append(
                f"It was written at {handoff_written} (UTC). The reader is the "
                "one who deletes a handoff, so delete it once you have taken in "
                "its contents. Otherwise the next session is told about it "
                "again. If that timestamp is not recent, treat the contents as "
                "possibly already acted on and check the repository before "
                "redoing anything it describes."
            )
    elif handoff_unknown:
        lines.append(
            "Whether a handoff from the previous session exists could not be "
            "determined: the probe for it failed, which is not the same as "
            "finding none. Look for one yourself before concluding there is "
            "nothing to resume."
        )
    elif crash_recovery:
        lines.append(
            "This session is being resumed because a handoff file could not be "
            "found for this project. Either a crash occurred or the previous session "
            "ended without the handoff being written. If you intended to end the "
            "session, please make sure you write a handoff first next time."
        )
    return " ".join(lines + _family(instance))


def _shown(record) -> str:
    # A backtick in a name would open a span that reads as a command.
    return f"{record.name.replace('`', chr(39))} ({record.id})"


def _family(instance: Instance) -> list:
    """Who started this operator, whom it started, and how to start more."""
    import config
    import lineage
    import operators
    records = operators.all_operators() or []
    me = next((op for op in records if op.id == instance.id), None)
    if me is None:
        return []
    up = lineage.parents(records)[me.id]
    parent = next((op for op in records if op.id == up), None)
    kids = lineage.children(me.id, records)
    lines = [f"You are operator {_shown(me)}. "
             + (f"Operator {_shown(parent)} started you and is your parent."
                if parent else "A person started you.")
             + (f" Your children are {', '.join(map(_shown, kids))}." if kids else "")]
    if lineage.depth(me.id, records) < config.max_depth():
        lines.append(
            "To start a child operator that works in your checkout, run "
            '`operator start NAME "..."`. To give it a git worktree of this '
            "project instead, create the worktree with git, then run "
            '`operator start NAME --dir PATH "..."`. Starting a stopped child '
            "by name restarts it.")
    else:
        lines.append("You are as deep as operators may go, so you cannot start children.")
    lines.append("To stop a child and every operator under it, run "
                 "`operator stop NAME`. To see them all, run `operator list`.")
    return lines
