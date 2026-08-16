"""The kernel's one point of contact with a work store it does not own.

Supervision is this kernel's subject; deciding what an agent should work on is
not, and `operator_session` sits on `test_kernel_boundary.FORBIDDEN` to say so.
But a supervisor still has to *ask* — FR-2 wants the assignment settled before
the agent's first token, and the only party that can do that is the one
launching it. So there is a seam, and this module is it: five functions, an
injected store, and no import of anything on the far side.

Extracted from `supervisor.py` when that file reached `MAX_MODULE_LINES`
exactly and the extension host still had to be wired in. The budget did what it
is for — it refused an addition and made somebody name a seam rather than let
the file grow one more reasonable-looking increment. This is the seam it named.
Everything here was already `_loop_`-prefixed and called from one place, which
is what made it separable without moving any decision.

The whole contract is that supervision works without a store. No store means no
assignment clause and no claim, and never means no session.
"""
from __future__ import annotations

from pathlib import Path

import claims
from paths import project_dir
from paths import catalog_guid
from probes import log

#: The work-item store, injected by whoever starts the loop. ``None`` means no
#: store was supplied and the assignment features are simply off.
#:
#: A hook rather than an import because the store is on the far side of the
#: kernel boundary -- `operator_session` is on `test_kernel_boundary.FORBIDDEN`,
#: since deciding what an agent should work on is not supervising it.
#:
#: It was neither, until a reviewer's remark sent me looking: both functions
#: below called `operator_session` as a bare name nothing imported, so every
#: call raised `NameError`, both handlers caught it, and the subsystem
#: answered "no assignment" for its whole life. An import scan cannot see a
#: bare name. Nothing calls `set_session_store` yet -- test_work_assignment.py.
_SESSION_STORE = None


def session_store():
    return _SESSION_STORE


def set_session_store(store) -> None:
    """Supply the work-item store, or ``None`` to run without one.

    Called by the entry point, not by kernel code. The kernel's half of the
    contract is that it works without one: no store means no assignment clause
    and no claim, and never means no session.
    """
    global _SESSION_STORE
    _SESSION_STORE = store


def _loop_work_db(workdir: Path):
    """The claim/session database for the project being supervised, or ``None``.

    Quiet and total, unlike its CLI equivalent ``_session_db``: the loop must
    launch a session whether or not this project is registered, so every
    failure here becomes ``None`` and a log line rather than an exception.
    Resolved from the *primary* checkout so a loop running inside a worktree
    finds the project's real entry. A missing store *says so*: unannounced, it
    is the "no assignment" an empty queue gives, and only one is a fault.
    """
    store = session_store()
    if store is None:
        log("  No work store is configured, so assignment is off -- which is "
            "not the same as having no work assigned.")
        return None
    try:
        # `workdir`, not `primary_repo_root(workdir)`: `catalog_guid` resolves
        # the primary checkout itself, so a worktree finds the project's real
        # entry either way and only one of the two spellings says where that
        # decision lives.
        found = catalog_guid(workdir)
        if found.undecided:
            # "Could not settle it" is not "not registered", and this whole
            # subsystem's failure mode is answering the second when it means
            # the first. An unreadable catalog, a working directory that will
            # not resolve, or a row that could not be compared all land here,
            # and every one of them would otherwise reach the agent as "you
            # have no assignment" -- indistinguishable from an empty queue.
            log("  Could not settle which project this is, so no work database "
                "was opened -- which is not the same as having no work.")
            return None
        if found.guid is None:
            return None
        return store.db_path(project_dir(found.guid))
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not resolve this project's work database ({exc})")
        return None


def _loop_start_session(db, instance: "Instance", session_num: int):
    """Open the session log and settle what this instance is to work on.

    FR-2 wants the assignment resolved before the agent's first token, and the
    only party that can do that is the one launching it. An agent left to work
    it out for itself pays for the reasoning on every session, can still get
    it wrong, and needs the rules in its context permanently to get it right.
    Here it is one query whose answer is already in the preamble.

    Total for the same reason as :func:`_loop_work_db`: a missing assignment
    costs the agent a hint, and must not cost it a session.
    """
    if db is None:
        return None
    store = session_store()
    if store is None:
        return None
    try:
        store.init_db(db)
        return store.start_session(
            db, instance=instance.id, session=session_num)
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not resolve this session's assignment ({exc})")
        return None


def _loop_heartbeat(db, instance_id: str) -> None:
    """Refresh whatever claim this instance currently holds.

    The supervisor heartbeats, not the agent. It is the only party that knows
    the session is alive from the process table rather than from the agent's
    opinion of its own progress -- an agent asked to report its own liveness
    reports it right up to the moment it stops being able to, which is the
    only moment the answer mattered.

    The claim is re-read rather than remembered from the assignment, because
    an agent can take one mid-session; caching the item resolved at launch
    would leave exactly those claims un-refreshed until they went stale, and
    the whole point of the cascade is that a stale claim gets taken away.
    """
    if db is None:
        return
    try:
        held = claims.claim_for_instance(db, instance_id)
        if held is not None:
            claims.heartbeat(db, item=held.item, instance=instance_id)
    except Exception as exc:                                # noqa: BLE001
        log(f"  Could not refresh this instance's work claim ({exc})")
