"""The kernel's one point of contact with code somebody else installed.

`extensions.py` could ask an extension a question; nothing called it. This is
the seam that does, for the one in-loop hook a supervisor has a use for today:
`admit_launch` — *may this seat start a session now?*

A separate module for the same reason `work_seam.py` is one: `MAX_MODULE_LINES`
refused an addition to `supervisor.py` at exactly 800 lines, and the answer to
that is to name the seam rather than raise the constant. Everything about
*asking* lives here; what stays in the supervisor is the one `if` it takes from
the answer.

**The host is built once per run, and that is load-bearing.** `Host`
quarantines an extension that overran its deadline for the life of the host,
and a hook that hangs once hangs again — so a host rebuilt per launch forgets
the quarantine and pays a full `DEFAULT_DEADLINE` on every session for the rest
of the run.

**Fail-open, and here that is an absence rather than a policy.** An extension
that refuses is honoured; one that crashed, hung, or was never discoverable
refuses nothing. Nothing in this kernel fails closed on an extension's absence,
so there is no safety property left for fail-open to endanger
(`docs/extensions.md` §D-3). If you want a launch *stopped* by something, the
check belongs in the kernel, not behind this seam.

**A refusal's reason goes to the ledger and nowhere else.** Every admission is
recorded as a `claim.*` — attributed, unverified, invariant 5. The reason is an
extension's prose, INV-AUTH is about extension prose reaching an agent, and the
operator log is a file an agent can open; so the log names who refused and not
what they said. The ledger is for the human asking *why is this seat not
launching*.

**And the ledger is deduplicated on state change**, which is not tidiness. A
quiet-hours extension refuses every `RESTART_PAUSE_SECONDS` until morning: that
is one decision, and recording it four hundred times buries the moment it
changed its mind.
"""
from __future__ import annotations

import evidence
import extensions
from config import OPERATOR_HOME
from probes import log


class LaunchGate:
    """Asks installed extensions whether this seat may launch, and remembers.

    Holds three things across the whole run: the host (so a quarantine
    survives), what discovery could not make sense of (so an extension that
    was never askable is still reported), and the last state written to the
    ledger (so a standing refusal is recorded once rather than per pause).
    """

    def __init__(self, host=None, failures=(), home=None) -> None:
        self.host = host
        self.failures = tuple(failures)
        self.home = OPERATOR_HOME if home is None else home
        self._last = None

    def admits(self, *, instance: str, session: int, **facts):
        """Ask, record, and answer. Never raises, and never blocks for long.

        `facts` are the call site's to choose and are serialised before any
        process is spawned, so a live object cannot cross the boundary — see
        `Host.call`. Pass values, never the `Instance`, the work database or a
        state-directory path.

        Returns an `Admission`. With nothing installed that is an empty one,
        which admits: the caller's `if` reads the same either way, and there is
        no branch in the supervisor for "extensions are not a thing here".
        """
        if self.host is None:
            return extensions.Admission()
        claims, failures = self.host.call(
            "admit_launch", instance=instance, session=session, **facts)
        verdict = extensions.launch_admission(claims, failures)
        # Discovery failures join the blind, because from a launch's point of
        # view "registered but malformed" and "asked and could not answer" are
        # the same event: something installed had a say and did not get one.
        # They are folded into the *record* only — an extension that cannot be
        # asked cannot refuse, which is the fail-open rule and is not softened
        # by reporting it honestly.
        blind = tuple(f.extension for f in self.failures) + verdict.blind
        state = (int(session), verdict.admit, verdict.refusals, blind)
        if state != self._last:
            self._last = state
            evidence.record_launch_admission(
                self.home, instance=instance, session=session,
                admit=verdict.admit, refusals=verdict.refusals, blind=blind)
        return verdict


def launch_gate(home=None) -> LaunchGate:
    """Discover what is installed, once, and hold it for the run.

    Total by construction. Discovery reads entry-point metadata and imports
    nothing, and it is written not to raise — the backstop is here anyway,
    because this runs before the first session of an unattended run and the
    cost of being wrong about that is nine seats that never start.
    """
    try:
        found, failures = extensions.discover()
    except Exception as exc:                                # noqa: BLE001
        found, failures = [], [extensions.Failure(
            "<discovery>", "discover", type(exc).__name__, str(exc))]
    for failure in failures:
        # Said out loud once per run. An extension that is installed and never
        # asked is otherwise indistinguishable from one that was asked and had
        # nothing to say, which is this project's oldest failure.
        log(f"  Extension {failure.extension} cannot be asked about launches "
            f"({failure.error}: {failure.detail})")
    if found:
        log(f"  Extensions asked before each launch: "
            f"{', '.join(e.name for e in found)}")
    return LaunchGate(extensions.Host(found) if found else None, failures, home)
