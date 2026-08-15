"""Who a seat is, in git and in the ledger.

A *seat* is durable. It outlives the sessions that run in it — that split is the
one this kernel is built on, and it is why there is somewhere to stand while the
agent process is dead. So the seat's identity has to be durable too.

**A seat id is not derived from a session id**, and the reason is the split
above. A per-session identity mints a new author on every restart: hundreds of
one-off names in `git log`, no way to ask what a seat has done over a week, and
no per-seat history for effort estimates or calibration to be computed against
— which is the mechanism by which a seat improves rather than merely persists. A
session identifier belongs in a commit trailer as context, not in the author
field as identity.

So a seat id is a stable, human-meaningful name: the same name that appears on
the board and that you type at the command line. `validate_seat_id` refuses ids
that look session-derived, because the failure would otherwise be silent and
only visible months later as a log nobody can query.
"""
from __future__ import annotations

import re

#: The account that answers for the fleet. Commits carry it as a
#: `Co-authored-by` trailer: the seat is the author, this is who is accountable.
ACCOUNTABLE_HUMAN = "Darin Hoover <darinh@gmail.com>"

#: The address family agent commits use. `+agent-<seat>` keeps everything on one
#: account — no second GitHub user to administer per seat — while making the
#: identity unmistakably not a person's at a glance and in a grep.
EMAIL_TEMPLATE = "darin+agent-{seat}@users.noreply.github.com"

#: What a seat id may be. Lowercase, digits, hyphens; must start with a letter.
#: A leading letter is what stops a bare hash being a legal id.
SEAT_ID = re.compile(r"^[a-z][a-z0-9-]{1,38}$")

#: Shapes that are almost certainly a session identifier rather than a seat
#: name. Refused with an explanation rather than accepted quietly, because the
#: cost of getting this wrong is invisible until somebody asks a question the
#: history can no longer answer.
_SESSION_SHAPED = (
    re.compile(r"^[0-9a-f]{6,}$"),                       # a bare hex prefix
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-"),            # a uuid
    re.compile(r"(^|-)session(-|$)", re.I),
    re.compile(r"(^|-)\d{4,}$"),                          # trailing run number
)


class SeatIdError(ValueError):
    """A seat id that would not survive as an identity."""


def validate_seat_id(seat: str) -> str:
    """Return ``seat``, or raise :class:`SeatIdError` explaining the refusal."""
    if not SEAT_ID.match(seat):
        raise SeatIdError(
            f"{seat!r} is not a usable seat id: lowercase letters, digits and "
            f"hyphens, starting with a letter, 2-39 characters."
        )
    for pattern in _SESSION_SHAPED:
        if pattern.search(seat):
            raise SeatIdError(
                f"{seat!r} looks like a session identifier, not a seat. A seat "
                f"outlives its sessions; deriving its identity from one mints a "
                f"new author on every restart and makes 'what has this seat "
                f"done' unanswerable. Name it for the work it holds."
            )
    return seat


def seat_identity(seat: str) -> tuple[str, str]:
    """``(author name, author email)`` for a seat's git commits."""
    validate_seat_id(seat)
    return f"{seat} (agent)", EMAIL_TEMPLATE.format(seat=seat)


def commit_trailers(seat: str, session: int | None = None) -> list[str]:
    """Trailers every commit a seat makes must carry.

    The session number rides here rather than in the identity: it is useful
    context for reading one commit and useless as a name for the thing that
    made it.
    """
    validate_seat_id(seat)
    trailers = [f"Co-authored-by: {ACCOUNTABLE_HUMAN}"]
    if session is not None:
        trailers.append(f"Agent-session: {seat}#{session}")
    return trailers


def is_agent_identity(name: str, email: str) -> bool:
    """Whether this identity is a seat rather than a person.

    Deliberately a shape rather than a list: seats are created freely and a
    list of known names goes stale, silently, in the direction that lets a
    human identity through.
    """
    return bool(
        re.search(r"^[^@]*\+agent[^@]*@", email)
        or re.search(r"\(agent\)$", name)
    )
