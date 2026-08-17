"""Watch the ledger for seats that keep ending badly, and tell a human once.

The kernel already stops a seat that has ended too many sessions unexplained --
`MAX_UNACCOUNTED_SESSIONS` and the progress breaker both fire, and
`record_session_exit` writes `giving_up` when they do. What nothing does is
*tell anyone*. The supervisor exits, the loop is gone, and the next person to
notice is whoever wonders why that repository has been quiet since Tuesday. On
2026-08-03 seven loops died together and the only artifact was seven identical
lines in `operator.log`.

This is the observe-and-propose half of the design doing the one job it is
unambiguously right for. It decides nothing: an extension that could reclassify
an exit could make a crashed seat read as finished (§5), so nothing here touches
classification. It reads what the supervisor already concluded and puts it in
front of a person.

**Everything cumulative is on disk, because nothing else can be.**
`extensions.Host` spawns one process per call, so `on_fact` cannot remember the
batch before it in memory -- and that is a property worth having rather than a
limitation to work around: no extension can leave state behind that another
call reads. The consequence is that this module's state file *is* its memory,
and `activation.write_state` replaces it atomically because a fleet host that
restarts mid-write must not read half a JSON document and silently start
counting from zero.

**One proposal per seat per deterioration.** Proposing on every tick would put
the same sentence in the queue every five minutes; proposing only once ever
would go quiet on a seat that recovered and then failed again. So the count at
which a proposal was last made is remembered, and a proposal is made again only
when things get worse than that.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import activation

NAME = "seat-watch"

#: Consecutive unexplained endings before a human is told. Three rather than
#: one: a single unexplained exit is ordinary -- a laptop sleeping, a console
#: control event delivered to every process sharing the console -- and a queue
#: that reports ordinary things is one people stop reading.
DEFAULT_THRESHOLD = 3

#: How long a seat stays in the state file after its last record. A seat that
#: has not been heard from in a week is a repository somebody stopped working
#: on, and keeping it forever turns this file into a list of everything that
#: ever ran.
DEFAULT_FORGET_DAYS = 7

#: The ledger event this reads. One event, named as a constant, because a
#: rename in `evidence.py` should be a grep away rather than a silent stop:
#: this extension going quiet looks exactly like a fleet with nothing wrong.
EVENT = "session_exit"

#: The most seats reported in one call, so a fleet that all fell over at once
#: does not fill the queue in a single tick.
MAX_PER_CALL = 5


def _parse_ts(value) -> "datetime | None":
    """`2026-08-17T11:45:12Z` to an aware datetime, or None.

    Written out rather than using `fromisoformat` directly: Python 3.10 does
    not accept the trailing `Z`, and this package supports 3.10 because the
    kernel does.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _seats(state) -> dict:
    seats = state.get("seats")
    return dict(seats) if isinstance(seats, dict) else {}


def on_fact(**facts):
    """Fold a batch of ledger records into what is known about each seat.

    Returns `None` always. `FleetHost.deliver` discards `on_fact` claims, so
    returning anything else would be inventing a channel that does not exist --
    and a claim nobody reads is exactly the shape of signal this project keeps
    getting wrong.

    Delivery is at-least-once, so the same record can arrive twice. Nothing
    here accumulates: `consecutive` is *copied* from the record rather than
    incremented, because the supervisor already counted it and a counter that
    adds on redelivery would report seats failing that never did.
    """
    if activation.settings(NAME) is None:
        return None
    records = facts.get("facts")
    if not isinstance(records, list) or not records:
        return None

    state = activation.read_state(NAME)
    seats = _seats(state)
    changed = False
    for record in records:
        if not isinstance(record, dict) or record.get("event") != EVENT:
            continue
        instance = record.get("instance")
        if not isinstance(instance, str) or not instance.strip():
            continue
        consecutive = record.get("consecutive")
        if not isinstance(consecutive, int) or isinstance(consecutive, bool):
            continue
        entry = dict(seats.get(instance) or {})
        told = entry.get("told")
        if isinstance(told, int) and consecutive < told:
            # The seat recovered. Forget that it was ever reported, so a later
            # streak of the same length is reported again. Without this the
            # module did precisely what its own docstring says it must not:
            # went quiet on a seat that recovered and then failed again, unless
            # the new failure happened to be worse than the old one. The
            # supervisor writes `consecutive=0` on a healthy ending, so this is
            # the ordinary path rather than an edge case.
            entry.pop("told", None)
        entry["consecutive"] = consecutive
        entry["giving_up"] = record.get("giving_up") is True
        entry["ts"] = str(record.get("ts", ""))
        seats[instance] = entry
        changed = True

    if changed:
        activation.write_state(NAME, dict(state, seats=seats))
    return None


def on_tick(**facts):
    """Forget seats nothing has said anything about for a while.

    Housekeeping rather than observation, and it lives on the tick because the
    tick is the only thing that happens when the fleet is quiet -- which is
    exactly when a stale entry would otherwise sit in the file forever.
    """
    config = activation.settings(NAME)
    if config is None:
        return None
    days = config.get("forget_after_days", DEFAULT_FORGET_DAYS)
    if not isinstance(days, (int, float)) or isinstance(days, bool) or days <= 0:
        days = DEFAULT_FORGET_DAYS

    now = _parse_ts(facts.get("now")) or datetime.now(timezone.utc)
    state = activation.read_state(NAME)
    seats = _seats(state)
    keep = {}
    for instance, entry in seats.items():
        if not isinstance(entry, dict):
            continue
        seen = _parse_ts(entry.get("ts"))
        # A seat whose timestamp will not parse is kept, not dropped. Losing
        # the record of a failing seat because its clock format changed is the
        # worse of the two errors by a distance.
        if seen is not None and (now - seen).days > days:
            continue
        keep[instance] = entry
    if len(keep) != len(seats):
        activation.write_state(NAME, dict(state, seats=keep))
    return None


def propose_work(**facts):
    """Name every seat that has crossed the threshold since it was last named.

    KNOWN LIMITATION, found independently by two reviewers: `told` advances
    here, before `FleetHost` appends the proposal. An extension is one process
    per call with no acknowledgement channel, so a seat named into a queue that
    then refuses the append is recorded as told and is not named again until it
    deteriorates further. The host reports that append failure to
    `fleet-failures.jsonl`; closing the gap properly needs a reply the hook
    contract does not currently have.
    """
    config = activation.settings(NAME)
    if config is None:
        return None
    threshold = config.get("failures", DEFAULT_THRESHOLD)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        threshold = DEFAULT_THRESHOLD

    state = activation.read_state(NAME)
    seats = _seats(state)
    proposals = []
    changed = False
    for instance in sorted(seats):
        entry = seats[instance]
        if not isinstance(entry, dict):
            continue
        consecutive = entry.get("consecutive")
        if not isinstance(consecutive, int) or consecutive < threshold:
            continue
        told = entry.get("told")
        if isinstance(told, int) and consecutive <= told:
            continue
        entry["told"] = consecutive
        seats[instance] = entry
        changed = True
        gave_up = entry.get("giving_up") is True
        proposals.append({
            "title": (f"seat {instance} has ended {consecutive} sessions "
                      f"unexplained" + (" and has stopped" if gave_up else "")),
            "detail": (
                f"The supervisor for {instance} recorded {consecutive} "
                f"consecutive endings with no stop, detach or restart marker "
                f"set. "
                + ("It reached its limit and the loop is no longer running. "
                   if gave_up else
                   "The loop is still running and will stop if this "
                   "continues. ")
                + f"The records are in trace.jsonl under event={EVENT}, "
                f"instance={instance}."),
        })
        if len(proposals) >= MAX_PER_CALL:
            break

    if changed:
        activation.write_state(NAME, dict(state, seats=seats))
    return proposals or None
