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
        f"`operator handoff --instance {instance.id} "
        '--status "..." --next "..." --context "..."`.',
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
    return " ".join(lines)
