"""The fleet-level half of the extension system: what watches, not what supervises.

`operator_kernel/extension_seam.py` is the other half. It asks `admit_launch`
on a seat's launch path, where something is *waiting for the answer*. Nothing
here is. That is the whole distinction §D-2 of `docs/extensions.md` settled on,
and it is why this file is in `operator_fleet/` rather than in the kernel: a
supervision kernel that grows a ledger tailer to send a digest at 08:00 has
stopped being one, and the arrow says the same thing more plainly — this
imports four kernel modules and no kernel module imports it.

**The three hooks, and why each needs a host that is not a supervisor.**

`on_fact`   — the ledger tail. §3.1 is the reason it is not a hook a
              supervisor calls: a notifier doing `requests.post(webhook,
              timeout=30)` on the poll thread voids the supervision guarantee
              silently, and no timeout tuning fixes a design where observation
              is synchronous with supervision. Here there is no code path by
              which a notifier can delay a seat, because the process that reads
              the ledger is not the process that watches the seats.
`on_tick`   — R2 and R5. A nightly digest must run at 08:00 whether or not any
              seat is up, and the supervisor loop is per-seat, so a per-seat
              host silently assumes a running loop to be called from.
`propose_work` — R4 shaped by INV-WORK. It proposes to a queue a human drains.
              It cannot claim, lease, or approve, and there is no field in the
              record this file writes that spells "approved".

**What is deliberately not built here, named rather than left as a gap.**

*R6, extensions talking to each other through the ledger.* That needs a write
path from an extension's answer back into the ledger this file tails, and the
obvious version of it is an amplifier: an extension that emits on every fact
emits on its own emission, forever, at one interpreter start per round. The
value an `on_fact` hook returns is therefore read and dropped. A safe version
needs a channel the tail does not read, and that is a decision, not an
oversight.

*A per-extension declared schedule.* R5 wants "daily at 08:00" declared and
woken rather than polled, and the closed hook set has no question that asks an
extension when it wants waking. What this gives instead is the wall-clock time
on every tick: the extension does not poll — it holds no process and burns
nothing between ticks — but it decides for itself whether its 08:00 has passed,
at the granularity of `FLEET_TICK_INTERVAL`. A fourth hook is the honest alternative
and the set is closed for a reason, so this is written down rather than added.

**Fail-open is not a policy here either, it is an absence.** Nothing in this
file is on anybody's critical path, so an extension that hangs, crashes or was
never discoverable costs the fleet its *observation* and costs supervision
nothing at all. That is §D-2's last row, and it is the reason this host can
afford deadlines an order of magnitude longer than the launch path's.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import evidence
import extensions
from probes import log, marker_set, utcnow

#: The questions this host asks. Closed, and *disjoint* from the kernel's
#: `extensions.HOOKS` rather than a superset of it: the two hosts exist because
#: the two jobs have different failure modes, and a hook askable from both
#: would be one whose author cannot know whether a seat is waiting on it.
FLEET_HOOKS = ("on_fact", "on_tick", "propose_work")

#: Seconds one fleet worker gets, enforced by killing it. Six times the launch
#: path's, because the constraint there was a seat sitting unsupervised and
#: there is no such seat here — a digest that takes half a minute to render is
#: doing its job, not hanging.
FLEET_DEADLINE = 60.0

#: Seconds one `call` may take across every installed extension. The same
#: reasoning as the kernel's: the number of registered entry points is not this
#: file's to bound, so without a total the worst case is that number times the
#: per-worker deadline.
FLEET_CALL_DEADLINE = 300.0

#: How many ledger records one `on_fact` delivery may carry. A bound on the
#: payload rather than on the tail: what is left over is delivered by the next
#: poll, because the offset advances only past records that were handed over.
MAX_FACTS_PER_POLL = 500

#: Proposals accepted from one extension in one call. INV-WORK's arithmetic
#: half: backlog 0014 was work being manufactured faster than it was refused,
#: and an extension that answers with ten thousand items is that failure with a
#: supply chain attached. Excess is dropped and recorded against the extension.
MAX_PROPOSALS = 20

#: Characters of one proposal kept. It is a queue entry for a human to read,
#: not a document, and it is third-party text on its way to a file somebody
#: opens.
PROPOSAL_CHARS = 500

#: Seconds between `on_tick` calls, and with them the proposal round.
FLEET_TICK_INTERVAL = 300.0

#: Seconds between ledger polls. Short, because it costs a `stat` and a read
#: when nothing has happened; the interpreter starts only when there is
#: something to deliver.
FLEET_POLL_INTERVAL = 15.0


def proposals_path(home) -> Path:
    """The NEEDS-HUMAN queue. Append-only, and nothing here ever reads it back.

    A separate file from `trace.jsonl` on purpose. Written into the ledger, a
    proposal would arrive at `on_fact` on the next poll, and an extension that
    proposes in response to what it observes would then be observing itself.
    """
    return Path(home) / "proposals.jsonl"


def tail_state_path(home) -> Path:
    """Where the tail remembers how far it read, so a restart is not a choice
    between replaying the whole ledger and skipping what arrived while it was
    down."""
    return Path(home) / "fleet-tail.json"


def fleet_stop_marker(home) -> Path:
    return Path(home) / "fleet.stop"


@dataclasses.dataclass(frozen=True)
class Proposal:
    """One proposed work item, attributed and already vetted.

    `text` has been through `extensions.claim_text`, so every physical line of
    it carries the extension's name and the word `unverified` — INV-AUTH, and
    the same treatment an agent's assertion gets. Frozen, because provenance
    that can be rewritten after the fact is not provenance.
    """

    extension: str
    text: str
    withheld: tuple = ()


@dataclasses.dataclass(frozen=True)
class Poll:
    """What one round of the fleet host did, for a caller that wants to know."""

    delivered: int = 0
    ticked: bool = False
    proposals: tuple = ()
    failures: tuple = ()


class LedgerTail:
    """An incremental reader over `trace.jsonl`, written not to raise.

    Three properties, and each is one of §8's assumptions made true rather than
    assumed:

    **At-least-once.** The persisted offset advances only after the records it
    covers have been handed to `deliver`'s caller. A crash mid-delivery
    redelivers; it never skips.

    **Whole records only.** `evidence._append` writes a line with one `write`,
    but "one write" is not "atomic" — so a read that ends mid-line stops at the
    last newline and leaves the remainder for the next poll. Without that, one
    torn read is a permanently corrupted offset.

    **Rotation is a rename, and a rename is not a shrink.** The ledger rotates
    at 8 MB by renaming itself to `trace.jsonl.1`, so the file at this path is
    then a *different file* that happens to have the same name. Detecting that
    by `size < offset` — which is what the first version of this class did, and
    what the obvious tailer does — misses the case the rotation test was
    written for: a new ledger can be *longer* than the old tail's offset within
    one poll, and the read then starts partway into an unrelated file and
    reports nothing wrong. So the file is identified by `(st_dev, st_ino)`, and
    a changed identity is the rotation. The remainder of the file that rotated
    away is delivered first; a tailer that reset to zero would drop everything
    between its offset and 8 MB, silently, which is the lossy tail §8 says
    weakens the whole observe-off-thread story.
    """

    def __init__(self, path, state=None) -> None:
        self.path = Path(path)
        #: `with_suffix` and not `+ ".1"` on the string, so this tracks
        #: `evidence._rotate_if_needed` exactly rather than approximately.
        self.rotated = self.path.with_suffix(self.path.suffix + ".1")
        self.state = None if state is None else Path(state)
        self.unreadable = 0
        self.gaps = 0
        self.offset, self.identity = self._resume()

    def read(self, limit: int = MAX_FACTS_PER_POLL) -> "list[dict]":
        """Every record appended since the last read, oldest first."""
        lines: list[str] = []
        current = self._identity(self.path)
        if self.offset and self.identity is not None \
                and current != self.identity:
            lines.extend(self._rotated_remainder(limit))
            self.offset = 0
        elif self._size(self.path) < self.offset:
            # Same file, fewer bytes: somebody truncated the ledger in place.
            # Nothing renamed it, so there is no copy to finish and the records
            # between here and there are gone. Counted rather than passed over,
            # because the whole point of this class is that a gap in the
            # evidence is itself evidence.
            self.gaps += 1
            self.offset = 0
        self.identity = current
        remaining = limit - len(lines)
        if remaining > 0:
            fresh, self.offset = self._read_from(self.path, self.offset,
                                                 remaining)
            lines.extend(fresh)
        return [r for r in (self._parse(line) for line in lines)
                if r is not None]

    def remember(self) -> bool:
        """Persist the offset. Returns whether it was written.

        Called by `deliver` after the batch is out, never by `read`, because
        the gap between those two calls is exactly where at-least-once lives.

        The file's identity is persisted with it. Without that, a rotation
        while this process was *down* is invisible on the next start: the
        offset would be applied to whatever file now holds the name.
        """
        if self.state is None:
            return False
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps(
                {"path": str(self.path), "offset": int(self.offset),
                 "identity": list(self.identity or ())}), encoding="utf-8")
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _resume(self) -> "tuple[int, tuple | None]":
        """The remembered position, or the beginning — and the beginning on any
        doubt.

        The recorded path is checked against the one being followed. A state
        file left by a tail of a *different* ledger would otherwise seek this
        one to an offset that means nothing in it, and the read would begin in
        the middle of a record. Re-reading is at-least-once; seeking into the
        wrong file is neither.
        """
        if self.state is None:
            return 0, self._identity(self.path)
        try:
            saved = json.loads(self.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if not isinstance(saved, dict) or saved.get("path") != str(self.path):
            return 0, self._identity(self.path)
        try:
            offset = max(0, int(saved.get("offset", 0)))
        except (TypeError, ValueError):
            return 0, self._identity(self.path)
        identity = saved.get("identity")
        if not isinstance(identity, list) or len(identity) != 2:
            # No identity to compare, so a rotation that happened while this
            # process was down cannot be detected. Start over rather than seek
            # a file that may not be the one the offset was measured in.
            return 0, self._identity(self.path)
        return offset, tuple(identity)

    def _rotated_remainder(self, limit: int) -> "list[str]":
        """What was still unread in the file that has just been rotated away."""
        if self._identity(self.rotated) != self.identity:
            # Two rotations between polls, or a rename to somewhere this does
            # not look. Either way records existed that this tail will never
            # see, and a counter is the difference between a gap and a silence.
            self.gaps += 1
            return []
        lines, _ = self._read_from(self.rotated, self.offset, limit)
        return lines

    @staticmethod
    def _identity(path: Path) -> "tuple | None":
        """`(device, inode)`, or `None` when there is no file to identify.

        Populated on Windows as well as POSIX — `os.stat` fills `st_ino` from
        the file index there — which matters because this is the platform the
        rest of this project is most careful about.
        """
        try:
            info = path.stat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino)

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _read_from(path: Path, offset: int, limit: int
                   ) -> "tuple[list[str], int]":
        """Whole lines from `offset`, and the offset just past the last of them.

        Bytes rather than text, and `errors="replace"` rather than `strict`.
        The ledger is written `ensure_ascii=False`, so it carries real UTF-8,
        and a decode error while *reading evidence* would stop the tail on
        exactly the record most worth reading. It is the same correction the
        worker's reader already carries one package over.
        """
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return [], offset
        if not data:
            return [], offset
        complete = data.split(b"\n")[:-1]
        if len(complete) > limit:
            complete = complete[:limit]
        consumed = sum(len(chunk) + 1 for chunk in complete)
        return ([chunk.decode("utf-8", "replace") for chunk in complete],
                offset + consumed)

    def _parse(self, line: str) -> "dict | None":
        try:
            record = json.loads(line)
        except ValueError:
            self.unreadable += 1
            return None
        if not isinstance(record, dict):
            self.unreadable += 1
            return None
        # Invariant 5 travels with the record. The ledger marks a claim; a
        # record with no `kind` is one the supervisor observed, and an
        # extension reading these must be able to tell the two apart without
        # knowing which events happen to be which.
        record.setdefault("kind", "fact")
        return record


class FleetHost:
    """Tails, wakes and collects — and disposes of nothing.

    The host is built once and held for the run, for the same reason
    `LaunchGate`'s is: `extensions.Host` quarantines an extension that overran
    its deadline for the life of the host, and one rebuilt per round forgets
    and pays a full deadline again every time.
    """

    def __init__(self, host=None, *, home, tail=None,
                 tick_interval: float = FLEET_TICK_INTERVAL,
                 clock=time.monotonic) -> None:
        self.host = host
        self.home = Path(home)
        self.tail = LedgerTail(evidence.trace_path(self.home),
                               tail_state_path(self.home)) if tail is None \
            else tail
        self.tick_interval = tick_interval
        self.clock = clock
        self._last_tick = None

    # ── the three hooks ─────────────────────────────────────────

    def deliver(self, limit: int = MAX_FACTS_PER_POLL) -> "tuple[int, list]":
        """Hand every new ledger record to `on_fact`, oldest first.

        One call carrying a batch, not one call per record, and that is a
        departure from the shape the name suggests — recorded here rather than
        smoothed over. `docs/extensions.md` already makes the same trade in the
        other direction ("one worker per extension" implemented as one worker
        per *call*), and the arithmetic decides it: a process per record means
        five hundred interpreter starts to drain one poll, which on a busy
        fleet is a tail that falls permanently behind. Order is preserved and
        delivery is still at-least-once, which is what §8 actually assumed.
        """
        records = self.tail.read(limit)
        if not records:
            return 0, []
        _, failures, asked = self._ask("on_fact", facts=records)
        if asked:
            # Advanced on a call that *happened*, not on one that succeeded. An
            # extension that crashed on this batch is fail-open's business and
            # gets the next one; holding the tail until it succeeds would make
            # one poison record stop every later fact from being observed. A
            # call that reached nobody is the other case, and is the one
            # at-least-once exists for.
            self.tail.remember()
        return len(records), failures

    def tick(self, force: bool = False) -> "tuple[bool, list]":
        """Wake every extension if the interval has passed. Returns whether it did.

        The first call always fires: a fleet host that has just come up has no
        idea how long it was down, and an extension that keeps its own schedule
        needs the wall clock to find out.
        """
        now = self.clock()
        if (not force and self._last_tick is not None
                and now - self._last_tick < self.tick_interval):
            return False, []
        elapsed = 0.0 if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        _, failures, _ = self._ask("on_tick", now=utcnow(),
                                   elapsed=round(elapsed, 3))
        return True, failures

    def propose(self, **facts) -> "tuple[list, list]":
        """Ask for work proposals, vet them, and write them where a human looks.

        Returns `(proposals, failures)`. Nothing is claimed, leased, scheduled
        or approved by anything on this path — the kernel's atomic lease is the
        only thing that can turn one of these into work, and it is reached from
        a human's decision rather than from here. Two seats may later be
        *offered* the same item; only one can win the row, so §3.2's double
        assignment stays unrepresentable rather than discouraged.
        """
        claims, failures, _ = self._ask("propose_work", **facts)
        proposals = self._vetted(claims, failures)
        for proposal in proposals:
            if not self._record(proposal):
                failures.append(extensions.Failure(
                    proposal.extension, "propose_work", "QueueUnwritable",
                    f"could not append to {proposals_path(self.home).name}"))
        return proposals, failures

    # ── running ─────────────────────────────────────────────────

    def poll_once(self) -> Poll:
        """One round: deliver what arrived, and on a tick boundary, wake and ask."""
        delivered, failures = self.deliver()
        ticked, tick_failures = self.tick()
        failures.extend(tick_failures)
        proposals: list = []
        if ticked:
            proposals, propose_failures = self.propose()
            failures.extend(propose_failures)
        self._report(failures)
        return Poll(delivered, ticked, tuple(proposals), tuple(failures))

    def run(self, *, rounds: "int | None" = None,
            interval: float = FLEET_POLL_INTERVAL, sleep=time.sleep,
            stop=None) -> int:
        """Poll until told to stop. Never raises, and returns rounds completed.

        `stop` is a predicate so a caller can supply its own; the default is
        the marker file, which is how the rest of this project asks a loop to
        end. A round that explodes is logged and the next one is attempted:
        this process observing nothing is bad, and this process *exiting* is
        the fleet losing its observation permanently for one bad poll.
        """
        if stop is None:
            def stop() -> bool:
                return marker_set(fleet_stop_marker(self.home))
        done = 0
        while rounds is None or done < rounds:
            try:
                if stop():
                    break
            except Exception as exc:                        # noqa: BLE001
                log(f"  The fleet host could not read its stop marker ({exc})")
            try:
                self.poll_once()
            except Exception as exc:                        # noqa: BLE001
                log(f"  A fleet host round failed ({type(exc).__name__}) — "
                    f"observation resumes next poll")
            done += 1
            if rounds is None or done < rounds:
                sleep(interval)
        return done

    # ── internals ───────────────────────────────────────────────

    def _ask(self, hook: str, **kwargs) -> "tuple[list, list, bool]":
        """Ask, and turn anything that goes wrong into a recorded failure.

        The third value is whether the question *reached* the workers, which is
        not the same as whether they answered well. With nothing installed
        there is nobody to ask and the question is nonetheless asked as far as
        this file is concerned; a host that raised on the way is the one case
        where no extension saw the payload, and `deliver` is the caller that
        has to tell those apart.
        """
        if self.host is None:
            return [], [], True
        try:
            claims, failures = self.host.call(hook, **kwargs)
        except Exception as exc:                            # noqa: BLE001
            return [], [extensions.Failure("<fleet>", hook, "HostError",
                                           repr(exc))], False
        return claims, failures, True

    def _vetted(self, claims, failures) -> "list[Proposal]":
        """Turn `propose_work` answers into proposals, refusing the rest.

        A shape that is not a list of objects with a title is an extension
        fault and is reported as one. Guessing at it is how "the extension
        proposed nothing" and "the extension proposed something unreadable"
        become the same event, which is this project's oldest failure.
        """
        accepted: list[Proposal] = []
        counts: dict[str, int] = {}
        for claim in claims:
            if claim.hook != "propose_work":
                continue
            if not isinstance(claim.value, list):
                failures.append(extensions.Failure(
                    claim.extension, "propose_work", "BadProposal",
                    f"propose_work returns a list of proposals, got "
                    f"{type(claim.value).__name__}"))
                continue
            for item in claim.value:
                title = item.get("title") if isinstance(item, dict) else None
                if not isinstance(title, str) or not title.strip():
                    failures.append(extensions.Failure(
                        claim.extension, "propose_work", "BadProposal",
                        "a proposal is an object with a non-empty `title`"))
                    continue
                seen = counts.get(claim.extension, 0)
                if seen >= MAX_PROPOSALS:
                    failures.append(extensions.Failure(
                        claim.extension, "propose_work", "TooManyProposals",
                        f"more than {MAX_PROPOSALS} proposals in one call; "
                        f"the rest were dropped"))
                    break
                counts[claim.extension] = seen + 1
                accepted.append(self._vet(claim.extension, title,
                                          item.get("detail")))
        return accepted

    @staticmethod
    def _vet(extension: str, title: str, detail) -> Proposal:
        """Attribute and scan one proposal's text. INV-AUTH, via the same path
        a preamble clause takes.

        Truncated *before* vetting, not after: `claim_text` labels every
        physical line, and cutting the result could remove the label from the
        line it was put in front of — which is the one-`\\n` defeat two
        reviewers found in `claim_text` itself, reintroduced downstream.
        """
        text = title.strip()
        if isinstance(detail, str) and detail.strip():
            text = f"{text}\n{detail.strip()}"
        rendered, withheld = extensions.claim_text([extensions.Claim(
            extension, "propose_work", text[:PROPOSAL_CHARS])])
        return Proposal(extension, rendered,
                        tuple(phrase for _, phrases in withheld
                              for phrase in phrases))

    def _record(self, proposal: Proposal) -> bool:
        """Append one proposal to the queue. Returns whether it was written.

        `evidence._append` rather than a local `open(..., "a")`, and the
        underscore is deliberate: rotation, the parent `mkdir` and the
        never-raises contract are exactly what a queue file needs, and a second
        implementation of them would drift from the first — the same argument
        `test_fleet_boundary.py` makes for importing the kernel's scanners
        instead of copying them.
        """
        return evidence._append(proposals_path(self.home), {
            "ts": utcnow(),
            "event": "work_proposed",
            "kind": "claim",
            "verified": False,
            # There is no spelling of an approved proposal in this record, and
            # that is INV-WORK in the shape rather than in a check somebody
            # has to remember to run. Approval is minted by a human elsewhere.
            "approved": False,
            "extension": str(proposal.extension),
            "text": proposal.text,
            "withheld": [str(phrase) for phrase in proposal.withheld],
        })

    @staticmethod
    def _report(failures) -> None:
        """Say that an extension could not answer, without saying who.

        The same refusal `extension_seam.launch_gate` makes: the operator log
        is a file an agent can open, and an extension's *name* is third-party
        content that `discover` already refuses when it grants authority.
        Repeating it into that file one module later would undo the refusal.
        """
        for failure in failures:
            log(f"  A fleet extension could not answer {failure.hook} "
                f"({failure.error}) — named in the queue, not here")


def fleet_host(home, **kwargs) -> FleetHost:
    """Discover what is installed, once, and hold it for the run.

    Total by construction, and the backstop is here anyway: `discover` imports
    nothing and is written not to raise, but this runs before the first poll of
    a process nobody is watching.
    """
    try:
        found, failures = extensions.discover()
    except Exception as exc:                                # noqa: BLE001
        found, failures = [], [extensions.Failure(
            "<discovery>", "discover", type(exc).__name__, str(exc))]
    for failure in failures:
        log(f"  An extension registration cannot be asked about the fleet "
            f"({failure.error}) — named in the ledger, not here")
    if found:
        log(f"  Extensions watching the fleet: "
            f"{', '.join(e.name for e in found)}")
    host = extensions.Host(found, hooks=FLEET_HOOKS, deadline=FLEET_DEADLINE,
                           call_deadline=FLEET_CALL_DEADLINE) if found else None
    return FleetHost(host, home=home, **kwargs)
