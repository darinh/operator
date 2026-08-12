"""The claim a seat holds, and the heartbeat the supervisor renews for it.

Extracted from a 16-definition claims module; the kernel uses four. The
supervisor renews the lease, never the agent -- a wedged seat must not be
able to hold work by doing nothing.
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sqlite_store import connect                            # noqa: E402


TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Claim:
    """One row of ``work_claims``."""

    item: str
    instance: str
    subproject: str = ""
    worktree: "str | None" = None
    branch: "str | None" = None
    boot_id: "str | None" = None
    mux_session: "str | None" = None
    pid: "int | None" = None
    pid_start: "str | None" = None
    claimed_at: str = ""
    heartbeat_at: str = ""
    #: Bumped by every write that touches the row. A timestamp cannot do this
    #: job: :data:`TS_FORMAT` has no sub-second field, so two writes inside one
    #: second are indistinguishable, and a compare-and-swap that compares
    #: values alone reads "nothing happened" at the exact moment something did.
    #: The counter makes every refresh visible whatever the clock says.
    revision: int = 0
    #: ``os.name`` of the machine that wrote the claim. Recorded because
    #: ``worktree`` is a path in *that* machine's syntax and nothing converts
    #: it: read on the other kind of system the string is not invalid, merely
    #: wrong, and a presence probe then reports somebody's live worktree as
    #: absent. Inferring the platform from the path's shape is guesswork that
    #: fails on the overlaps -- ``/temp/app`` is a legal spelling on both --
    #: so the writer states it instead. ``None`` means a claim older than this
    #: column, where the shape is all there is to go on.
    platform: "str | None" = None


def utcnow() -> str:
    return datetime.now(tz=timezone.utc).strftime(TS_FORMAT)


def parse_ts(value: "str | None") -> "datetime | None":
    """A stored timestamp as an aware datetime, or ``None`` if it will not read.

    ``None`` is a real answer and is never smoothed into "now" or "the epoch".
    A heartbeat that cannot be read is not a fresh one and not an ancient one;
    it is a claim whose age is unknown, and :mod:`operator_liveness` reports
    that rather than acting on it.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, TS_FORMAT)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _row_to_claim(row) -> Claim:
    return Claim(
        item=row["item"],
        instance=row["instance"],
        subproject=row["subproject"] or "",
        worktree=row["worktree"],
        branch=row["branch"],
        boot_id=row["boot_id"],
        mux_session=row["mux_session"],
        pid=row["pid"],
        pid_start=row["pid_start"],
        claimed_at=row["claimed_at"],
        heartbeat_at=row["heartbeat_at"],
        revision=row["revision"],
        platform=row["platform"],
    )


def _claim_for_instance(conn, instance: str) -> "Claim | None":
    row = conn.execute(
        "SELECT * FROM work_claims WHERE instance = ?", (instance,)).fetchone()
    return None if row is None else _row_to_claim(row)


def claim_for_instance(path, instance: str) -> "Claim | None":
    """What this instance is working, if anything.

    The resume case of ``operator session start``: an instance that comes back
    from a restart holding a claim resumes it rather than being offered work.
    """
    with connect(path) as conn:
        return _claim_for_instance(conn, instance)


def claims(path) -> "list[Claim]":
    with connect(path) as conn:
        return [_row_to_claim(row) for row in
                conn.execute("SELECT * FROM work_claims ORDER BY claimed_at, item")]


def heartbeat(path, *, item: str, instance: str, now=None) -> bool:
    """Refresh the claim's heartbeat. False when ``instance`` is not the owner.

    Owner-guarded, so a heartbeat from an agent that lost its claim to a
    reclaim cannot resurrect it: the update names both the item and the
    instance, and a row that no longer matches both is not touched.
    """
    stamp = now or utcnow()
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE work_claims SET heartbeat_at = ?, revision = revision + 1"
            " WHERE item = ? AND instance = ?", (stamp, item, instance))
        return cur.rowcount == 1
