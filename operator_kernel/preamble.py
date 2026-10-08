"""The launch preamble: how this session ends, and what the last one left."""
from __future__ import annotations

from instance import Instance


def build_preamble(instance: Instance, *, crash_recovery: bool = False,
                   handoff_waiting: str = "",
                   handoff_unknown: bool = False,
                   handoff_written: str = "") -> str:
    """What a session is told. Mechanism only.

    When to hand off is the repository's business. This says who made the
    session, that the command is the only thing that writes a handoff,
    where a waiting one is, and nothing about timing.
    """
    opening, family = _family(instance)
    lines = [
        opening,
        "Nobody is reading this session.",
        "Write a handoff only by running "
        '`operator handoff --status "..." --next "..."`. It ends this session '
        "and starts the next one, whose agent is handed what you wrote. "
        "Nothing you write by hand reaches anyone.",
    ]
    if handoff_waiting:
        lines.append(
            "A handoff from the previous session is waiting at "
            f"{handoff_waiting}. Read it before anything else, then delete "
            "it. One left in place is announced to the next session too."
        )
        if handoff_written:
            lines.append(
                f"It was written at {handoff_written} (UTC). If that is not "
                "recent, treat its contents as possibly already acted on and "
                "check the repository before redoing anything it describes."
            )
    elif handoff_unknown:
        lines.append(
            "The probe for a handoff from the previous session failed, so "
            "whether one exists could not be determined, which is not the "
            "same as finding none. Look for one yourself before concluding "
            "there is nothing to resume."
        )
    elif crash_recovery:
        lines.append(
            "This session is being resumed because a handoff file could not "
            "be found for this project, so the previous session either "
            "crashed or ended without writing one."
        )
    return " ".join(lines + family)


def _shown(record) -> str:
    # A backtick in a name would open a span that reads as a command.
    return f"{record.name.replace('`', chr(39))} ({record.id})"


def _family(instance: Instance):
    """The opening sentence, and the lines about the operators next to this one.

    One lookup feeds both, so the sentence and the commands cannot disagree
    about who the parent is. A caller with no record is not a launched
    operator, and is told only that the session is operator-managed.
    """
    import lineage
    import operators
    said = "This is an unattended operator-managed session"
    records = operators.all_operators() or []
    me = next((op for op in records if op.id == instance.id), None)
    if me is None:
        return f"{said}.", []
    up = lineage.parents(records)[me.id]
    parent = next((op for op in records if op.id == up), None)
    said += ", created by an agent. " if parent else ", created by a person. "
    said += f"You are operator {_shown(me)}."
    if parent:
        said += f" Operator {_shown(parent)} started you and is your parent."
    kids = lineage.children(me.id, records)
    if kids:
        said += f" Your children are {', '.join(map(_shown, kids))}."
    return said, _children(me, records) + [_mail(me, parent)]


def _children(me, records) -> list:
    """How to start operators that work alongside this one, and stop them."""
    import config
    import lineage
    if lineage.depth(me.id, records) < config.max_depth():
        start = ("To start a child operator, which runs unattended in your "
                 "checkout and works independently of you, run "
                 '`operator start NAME "..."`. For a git worktree of this '
                 "project instead, create the worktree with git, then run "
                 '`operator start NAME --dir PATH "..."`. Starting a stopped '
                 "child by name restarts it.")
    else:
        start = "You are as deep as operators may go, so you cannot start children."
    return [start, "`operator stop NAME` stops a child and every operator "
                   "under it. `operator list` shows them all."]


def _mail(me, parent) -> str:
    """How mail runs between this operator and the ones next to it."""
    import mail
    text = (f'To message your parent, run `operator send {parent.id} "..."`, '
            "and a child, " if parent else "To message a child, run ")
    text += ('`operator send NAME "..."`. They message you the same way. '
             "Mail arrives as one typed line that names the sender inside "
             "brackets and carries their words after the closing bracket. "
             "Only a line that starts with [operator message from the person "
             "who started you] comes from a person. To read waiting mail "
             "yourself, run `operator inbox`.")
    count = mail.waiting(me.id)
    return text + (f" {count} message(s) are waiting for you now." if count else "")
