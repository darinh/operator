"""The evidence log: who ran, how it ended, and what code observed it.

Extracted from a 21-definition evidence module; the kernel uses three of
them. `record_session_exit` is the one that matters -- the record whose
earlier version could not express the event it existed to detect.
"""
from __future__ import annotations
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from ledger_chain import Writer
from process_tree import ancestry  # noqa: F401  re-export until callers move


#: Rotate at this size. One line is a few hundred bytes, so this is on the
#: order of a hundred thousand invocations -- long enough to cover an incident
#: that unfolded over days, short enough not to become the largest file in
#: the directory.
_MAX_BYTES = 8 * 1024 * 1024

_chain_writer = None


def trace_path(operator_home: Path) -> Path:
    return Path(operator_home) / "trace.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_mandate_read(operator_home: Path, *, instance: str, session: int,
                        mandate=None) -> None:
    """Record the authority a session was launched with. Never raises.

    A `fact.*`, not a claim: the supervisor read a file and hashed it, which
    is an observation about its own behaviour.

    This exists because the kernel cannot *prevent* a mandate being edited. A
    seat runs under the owner's filesystem identity, so it can write the file
    that says what it may do -- and that gap does not close until agents get
    their own OS account, which is the open question in `docs/plan.md`. What
    the kernel can do meanwhile is make the edit *visible*: every launch
    records the digest of the text it used, so a mandate that changes shows up
    as a change in the ledger, with the session that ran under each version
    named beside it.

    That is a weaker guarantee than prevention and is deliberately not
    described as anything else. It converts a silent rewrite into one that can
    be found afterwards, which is exactly the difference backlog 0013 turned
    on: the sentence was discoverable only because git had kept it.
    """
    try:
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "mandate_read",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            # None rather than a placeholder digest: "no mandate" and "a
            # mandate whose text happens to be empty" are different states,
            # and only the first means the session was told it had no grant.
            "present": mandate is not None,
            "author": getattr(mandate, "author", None),
            "recorded": getattr(mandate, "recorded", None),
            "source": getattr(mandate, "source", None),
            "digest": getattr(mandate, "digest", None),
        })
    except Exception:
        return
def record_handoff_state(operator_home: Path, *, instance: str, session: int,
                         verdict: str, path=None, announced: bool = False) -> None:
    """Record what the launcher established about the waiting handoff.

    A `fact.*`: the supervisor probed a path and composed a sentence from what
    it found.

    The same argument as `record_mandate_read`, one file over. The kernel
    cannot *make* a session read its handoff -- it can only put the address in
    the launch text and carry on. What it can do is write down which of the
    four situations obtained, so that "the session was told a handoff was
    waiting and ignored it" stops being indistinguishable from "there was
    nothing to read".

    Those two were genuinely indistinguishable until now, and the cost was
    measured rather than imagined: on 2026-08-15 a session was launched 6
    seconds after its predecessor wrote a handoff, never opened it, and spent
    its life on self-assigned work in a frozen repository. Nothing in any log
    on the machine recorded that a handoff had been available, so the failure
    could only be reconstructed afterwards by hand from file mtimes.

    ``verdict`` is one of the ``HANDOFF_*`` constants in `exits`, passed as a
    string rather than imported, because evidence records what it is told and
    importing the classifier here would give this module an opinion about
    supervision.
    """
    try:
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "handoff_state",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "verdict": str(verdict),
            # Whether a clause about it actually reached the launch text. The
            # verdict is what the supervisor observed; this is what the session
            # was told, and the gap between the two is where "it ignored the
            # handoff" and "nobody mentioned the handoff" used to be the same
            # record.
            "announced": bool(announced),
            # The address the session was given, so a later reader can open
            # the same file rather than infer which one was meant.
            "path": None if path is None else str(path),
        })
    except Exception:
        return


def record_launch_admission(operator_home: Path, *, instance: str, session: int,
                            admit: bool, refusals=(), blind=()) -> bool:
    """Record what installed extensions said about launching. Never raises.

    A `claim.*` and never a `fact.*` — invariant 5 of `docs/extensions.md`. The
    supervisor observed only that it asked and what came back, so `kind` and
    `verified` say that as fields rather than leave it implied by the event.

    Returns whether the line was appended, which no other recorder here does
    and this one must: its caller deduplicates on state, so reading a silent
    failure as a write would suppress every later record of the same state and
    turn deduplication into concealment.

    This is the only place a refusal's *reason* is kept — not the operator log,
    which is a file an agent can open. `blind` is the half that is easy to
    drop: two extensions asked and neither able to answer is a different launch
    from two that agreed, though the kernel launches in both. Each entry
    carries its error kind, because a name alone cannot say whether that
    extension will ever be asked again.
    """
    try:
        return _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "launch_admission",
            "kind": "claim",
            "verified": False,
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "admit": bool(admit),
            "refusals": [{"extension": str(name), "reason": str(reason)}
                         for name, reason in refusals],
            "blind": [{"extension": str(name), "error": str(error)}
                      for name, error in blind],
        })
    except Exception:
        return False


def record_withheld_clause(operator_home: Path, *, instance: str, session: int,
                           source: str, phrases) -> None:
    """Record text that tried to grant authority and was withheld. Never raises.

    A `fact.*`: the supervisor scanned a clause it was handed and removed it.

    This is where the withheld wording goes. It is deliberately not in the
    preamble -- quoting the caught phrase back into the text made the final
    scan fire on the refusal note itself -- and it has to be *somewhere*, or
    refusing a grant is indistinguishable from never having seen one.

    Worth reading when it appears. The only clause that can currently trip it
    comes from the work database, which agents write, so a record here means
    something wrote a permission grant into a work item. That is either a
    confused agent or a deliberate one, and both are worth knowing about.
    """
    try:
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "withheld_clause",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "source": str(source),
            "phrases": list(phrases),
        })
    except Exception:
        return


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    try:
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _stamp_chain(record: dict) -> dict:
    global _chain_writer
    if _chain_writer is None:
        _chain_writer = Writer(f"{os.getpid()}-{secrets.token_hex(8)}")
    return _chain_writer.stamp(record)


def _ledger(operator_home, record: dict) -> bool:
    return _append(trace_path(Path(operator_home)), record, chain=True)


def _append(path: Path, record: dict, *, chain: bool = False) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        if chain:
            record = _stamp_chain(record)
        line = json.dumps(record, ensure_ascii=False, default=str)
        # `newline=""` so the one separator written is the one byte counted.
        # Text mode translates "\n" to "\r\n" on Windows, and every caller that
        # budgets for a record ahead of writing it -- `journal.remember` adds
        # `+ 1` for this separator -- was then short by a byte per record. That
        # let a journal finish 4,194,305 bytes into a 4,194,304 byte cap, which
        # the feature notes claimed could not happen. It also made every byte
        # offset in `trace.jsonl` differ by platform, under a tail whose whole
        # design is to key on exact offsets.
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def record_supervisor_start(operator_home: Path, *, instance: str,
                            session: int, code: "dict | None" = None) -> None:
    """Record that a loop supervisor came up, and on what code. Never raises.

    A supervisor imports the operator once and runs it for the whole run, so
    every record it later writes describes the code it started with, not the
    code on disk when the record was read. Without this event the two are
    indistinguishable: the fix that made ``session_exit`` report handoff
    endings landed at 19:36 on 2026-08-04 and every supervisor had started at
    13:28, so records written *after* the fix were still produced by
    instruments without it, and nothing in them said so.

    ``code`` is stamped rather than the mere version string, because the
    version only moves when deployed artifacts change -- that very fix bumped
    nothing, so a version field would have reported the stale supervisor and
    the fixed one as identical.
    """
    try:
        payload = {
            "ts": _utcnow(),
            "event": "supervisor_start",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
        }
        if isinstance(code, dict):
            payload["code"] = code.get("digest")
            payload["toolkit_version"] = code.get("version")
        _ledger(operator_home, payload)
    except Exception:
        return


def record_session_exit(operator_home: Path, *, instance: str, session: int,
                        pid: "int | None", markers: "dict",
                        consecutive: int, limit: int,
                        code: "str | None" = None) -> None:
    """Record that a supervised copilot session ended. Never raises.

    This is the event the evidence was built for and the one an invocation log
    cannot see. When seven loops died together on 2026-08-03 no operator
    command was run at all -- each supervisor was already inside its poll
    loop, so there was nothing to attribute. ``operator.log`` said "copilot
    exited unexpectedly" seven times and could say nothing else, because the
    supervisor never waits on the child: it polls liveness, and a process that
    is gone leaves no exit code behind to read.

    So "unexpected" here means only *unexplained* -- no stop, detach or
    restart marker was set. It is not evidence of a crash, and the distinction
    matters: the copilot logs for that incident end with an orderly
    ``[shutdown] Shutdown complete``, and the extensions died with
    ``0xC000013A`` (``STATUS_CONTROL_C_EXIT``), which is a console control
    event delivered to every process sharing the console rather than a fault
    in any one session. What is recorded here is therefore the observation and
    the marker states it was judged against, so a later reader can re-judge
    it. Nothing here decides what killed the session.

    Endings that *were* explained are recorded too, and that is not a cosmetic
    addition: for a long time only the unexplained branch called this, so
    every record carried ``restart=False`` and the evidence could be read -- was
    read -- as proving no session had ever ended by handoff. A population that
    excludes the cases you are trying to count cannot answer the question, and
    it does not look empty while failing to.

    ``code`` fingerprints the operator source the *supervisor* is running, so
    a later reader can scope a re-measurement to records from an instrument
    that had a given fix. Scoping by date cannot do this: a supervisor keeps
    the code it imported at startup, so records dated after a fix are still
    written by supervisors without it.
    """
    try:
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "session_exit",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "session_pid": pid,
            # Tri-state per marker: True set, False absent, None unreadable.
            # An unreadable marker is why the supervisor waits instead of
            # relaunching, so flattening it here would hide the reason.
            "markers": dict(markers),
            "consecutive": consecutive,
            "limit": limit,
            "giving_up": consecutive >= limit,
            "code": code,
        })
    except Exception:
        return


def record_session_cost(operator_home, *, instance: str, session: int) -> None:
    """Record a readable spend figure when a session ends. Never raises.

    Recording is independent of whether a ceiling is set. Unknown spend is
    not written as zero.
    """
    try:
        import spend
        from config import SPEND_CEILING
        figure = spend.seat_figure(operator_home, instance)
        if figure is None:
            return
        amount, unit, source = figure
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "session_cost",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "amount": amount,
            "unit": unit,
            "source": source,
            "ceiling": SPEND_CEILING,
        })
    except Exception:
        return


def record_progress_verdict(
        operator_home, instance, session, verdict, before, after,
        accounted, nochange_streak, unaccounted_streak,
        limit_nochange, limit_unaccounted) -> None:
    """Record what the supervisor concluded about a finished session.

    Never raises. The verdict used to live only in probes.log and the streak
    files, so the ledger could not say what was concluded or from which
    fingerprints. Both hashes travel with it so a later reader can recompute
    the verdict, and so a later record whose before differs from this after
    shows the tree moved underneath the conclusion.
    """
    try:
        _ledger(operator_home, {
            "ts": _utcnow(),
            "event": "progress_verdict",
            "pid": os.getpid(),
            "instance": str(instance),
            "session": session,
            "verdict": str(verdict),
            "before": before,
            "after": after,
            "accounted": bool(accounted),
            "session_num": session,
            "nochange_streak": nochange_streak,
            "unaccounted_streak": unaccounted_streak,
            "limit_nochange": limit_nochange,
            "limit_unaccounted": limit_unaccounted,
        })
    except Exception:
        return
