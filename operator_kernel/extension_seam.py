"""The kernel's one point of contact with code somebody else installed.

`extensions.py` could ask an extension a question; nothing called it. This is
the seam that does, for the one in-loop hook a supervisor has a use for today:
`admit_launch` — *may this seat start a session now?* The design is
`docs/extensions.md`; below is only what this file decides. It is a separate
module for the same reason `work_seam.py` is one: `MAX_MODULE_LINES` refused an
addition to `supervisor.py` at exactly 800 lines, and the answer to that is to
name the seam, not to raise the constant.

Three decisions live here. **The host is built once per run**, because `Host`
quarantines an extension that overran its deadline for the life of the host, so
one rebuilt per launch forgets and pays a full `DEFAULT_DEADLINE` every session.
**Fail-open is an absence rather than a policy**: an extension that crashed,
hung or was never discoverable refuses nothing, and nothing here fails closed
on an extension's absence, so a launch you want *stopped* needs a kernel check
(§D-3). **A refusal's reason goes to the ledger and nowhere else** — every
admission is a `claim.*`, attributed and unverified per invariant 5, and
deduplicated on state change so a quiet-hours window is one record rather than
four hundred. The operator log is a file an agent can open, so it names who
refused and never what they said.
"""
from __future__ import annotations

import evidence
import extensions
from config import OPERATOR_HOME, RESTART_PAUSE_SECONDS
from probes import log

#: The longest wait between re-asking a gate that keeps refusing. The launch
#: backoff's ceiling, and the same trade: often enough that a window opening is
#: noticed, rarely enough that one which stays shut is a process storm.
HELD_PAUSE_CAP = 60.0


class LaunchGate:
    """Asks installed extensions whether this seat may launch, and remembers.

    Three things survive the run: the host (so a quarantine does), what
    discovery could not make sense of, and the last state written to the
    ledger.
    """

    def __init__(self, host=None, failures=(), home=None) -> None:
        self.host = host
        self.failures = tuple(failures)
        self.home = OPERATOR_HOME if home is None else home
        self._last = None

    def admits(self, *, instance: str, session: int, **facts):
        """Ask, record, and answer. Never raises, and never blocks for long.

        `facts` are the call site's to choose and are serialised before any
        process is spawned, so a live object cannot cross the boundary. Pass
        values, never the `Instance`, the work database or a state path.

        Returns an `Admission`; with nothing installed, an empty one, which
        admits, so the caller's `if` reads the same either way.
        """
        claims, failures = ([], []) if self.host is None else self.host.call(
            "admit_launch", instance=instance, session=session, **facts)
        verdict = extensions.launch_admission(claims, failures)
        # Everything that had no say, with *why* beside it: from a launch's
        # point of view "registered but malformed" and "asked and could not
        # answer" are the same event. The kind travels with the name because a
        # `Deadline` is `Quarantined` by the next launch and never runs again,
        # while a `HostError` is retried every time. Never the failure's
        # *detail*, which is a package's prose and unbounded.
        blind = tuple((f.extension, f.error)
                      for f in tuple(self.failures) + tuple(failures))
        if self.host is None and not blind:
            # No third party in this launch, so nothing to attribute. Every
            # other combination is recorded -- a reviewer found this early
            # return one line higher, where a discovery producing *only*
            # failures went unrecorded, and an extension nobody could ask is
            # then indistinguishable from one nobody installed.
            return verdict
        state = (int(session), verdict.admit, verdict.refusals, blind)
        if state != self._last and evidence.record_launch_admission(
                self.home, instance=instance, session=session,
                admit=verdict.admit, refusals=verdict.refusals, blind=blind):
            # Advanced only on a write that happened: set before it, a briefly
            # unwritable ledger would suppress every later record of the same
            # state, and deduplication would be hiding the refusal rather than
            # compressing it.
            self._last = state
        return verdict


def held_pause(consecutive: int) -> float:
    """How long to wait before asking again after `consecutive` refusals.

    Growing, and capped. A refusal is re-asked rather than remembered, because
    the answer is a third party's and changes with the clock -- but at a flat
    `RESTART_PAUSE_SECONDS` a quiet-hours window holding nine seats overnight
    is a quarter of a million interpreter starts before anyone has worked.
    """
    return min(HELD_PAUSE_CAP, RESTART_PAUSE_SECONDS * max(1, consecutive))


def launch_gate(home=None) -> LaunchGate:
    """Discover what is installed, once, and hold it for the run.

    Total by construction: discovery imports nothing and is written not to
    raise, and the backstop is here anyway because this runs before the first
    session of an unattended run.

    A failure is announced once, without its name: `discover` refuses a name
    that grants authority precisely because names get repeated, so repeating
    one into a file an agent can open would undo that refusal one module later.
    The ledger names them, attributed and unverified.
    """
    try:
        found, failures = extensions.discover()
    except Exception as exc:                                # noqa: BLE001
        found, failures = [], [extensions.Failure(
            "<discovery>", "discover", type(exc).__name__, str(exc))]
    for failure in failures:
        log(f"  An extension registration cannot be asked about launches "
            f"({failure.error}) — named in the ledger, not here")
    if found:
        log(f"  Extensions asked before each launch: "
            f"{', '.join(e.name for e in found)}")
    return LaunchGate(extensions.Host(found) if found else None, failures, home)
