"""`operator send` and `operator inbox`: mail along one parent and child edge.

A person is the parent of each operator they start. The recipient's own
supervisor types its mail into its session. A person, who has no session,
reads theirs with `operator inbox`, and an operator may too.
"""
from __future__ import annotations

import sys

from . import family

SEND_USAGE = 'Usage: operator send NAME "message"'
INBOX_USAGE = "Usage: operator inbox"


def send(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    import lineage
    import mail
    from custody import Agent
    from operators import HUMAN, all_operators, find
    from probes import utcnow
    if rest[:1] in (["-h"], ["--help"]):
        print(SEND_USAGE)
        return 0
    target, body = (rest[0].strip(), rest[1:]) if rest else ("", [])
    text = " ".join(body[1:] if body[:1] == ["--"] else body).strip()
    if not target or target.startswith("-") or not text:
        print(SEND_USAGE, file=sys.stderr)
        return 2
    if len(text) > mail.LIMIT:
        return family.refuse("send", f"a message may hold {mail.LIMIT} characters, "
                                     f"and this one holds {len(text)}.")
    record = None if target.casefold() == HUMAN else find(target)
    if record is None and target.casefold() != HUMAN:
        print(f"No operator '{target}'.", file=sys.stderr)
        return 1
    who = family.caller("send")
    if who is None:
        return 1
    me, my_name = ((who.record.id, who.record.name) if isinstance(who, Agent)
                   else (HUMAN, HUMAN))
    to, name = (record.id, record.name) if record else (HUMAN, HUMAN)
    records = all_operators() or []
    if not lineage.may_message(me, to, records):
        return family.refuse("send", f"{name} is not your parent or your child, "
                                     "and mail goes only between those two.")
    relation = ("an operator you started" if to == HUMAN
                else "your parent" if lineage.parents(records)[to] == me else "your child")
    try:
        mail.post(to, {"from": me, "from_name": my_name, "to": to, "relation": relation,
                       "text": text, "sent": utcnow()})
    except OSError as exc:
        print(f"could not send: {exc}", file=sys.stderr)
        return 1
    print(f"sent to {name}")
    return 0


def inbox(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    import mail
    from custody import Agent
    from operators import HUMAN
    for arg in rest:
        if arg in ("-h", "--help"):
            print(INBOX_USAGE)
            return 0
        print(INBOX_USAGE, file=sys.stderr)
        return 2
    who = family.caller("inbox")
    if who is None:
        return 1
    me = who.record.id if isinstance(who, Agent) else HUMAN
    if me == HUMAN:
        # No supervisor puts back what a person's interrupted inbox claimed.
        mail.requeue_stale(me)
    read = 0
    while (claimed := mail.take(me)) is not None:
        path, message = claimed
        print(f"{message['sent']}  {mail.line(message)}")
        mail.filed(path)
        read += 1
    if not read:
        print("No messages.")
    return 0
