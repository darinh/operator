# Extensions — design, after a three-family council

**Status.** The in-loop half is built: `operator_kernel/extensions.py` and
`operator_kernel/extension_worker.py`, with `tests/test_extensions.py`. §1
through §5 are implemented or refused as written, except that the closed hook
set is the three in-loop questions only. **One of the three is wired in**:
`admit_launch` is asked on the supervisor's launch path through
`operator_kernel/extension_seam.py`, and `tests/test_launch_admission.py` is
the test of the wiring rather than of the mechanism. A refusal holds the
launch — same session number, no launch failure counted, nothing claimed or
saved — and is recorded in the ledger as a `claim.*` per invariant 5, once per
state change rather than once per pause. `gate_change` and `detect_repo` still
have no call site: there is no kernel merge gate to hang the first on, and
nothing yet asks the second. **The fleet host of §D-2 — `on_fact`, `on_tick`,
`propose_work` — is not built**, and when it is it goes outside
`operator_kernel/`: it is never on a critical path, and a supervision kernel
that grows a ledger tailer has stopped being one. The open items of §8 are
still open, and the last of them is still the one most likely to be fatal.

One departure from §1.7 worth naming, because it reads as a contradiction and
is not: *one worker per extension* is implemented as one worker per **call**.
That satisfies the reason for the rule more strongly than a persistent worker
would — no extension can leave state where another call reads it, and a hung
call cannot corrupt the next one's framing — at the cost of an interpreter
start per call, which these hooks can afford because they fire at a launch or
a gate rather than on the poll loop.

**What D-4 turned out to understate.** "Deadlines are enforceable because the
worker is a separate process" is true and was not sufficient. With the worker's
output on *pipes*, `subprocess.run` kills the worker at the timeout and then
drains those pipes with no timeout of its own — and a grandchild inherited the
handles, so the drain waits for the grandchild. Measured here: **20.11 seconds
to return from a 1.0-second deadline**, seat unsupervised throughout. The
worker's output goes to temporary files for that reason, which also bounds
memory: a hook printing in a tight loop wrote 375 MB in two seconds, and
through a pipe that is 375 MB of the supervisor's address space.

**And what §3.3 turned out to understate.** A gate can be turned from *no* into
*could not run* without any bug in the gate, by three routes that all had to be
closed. Decoding the worker's output as the machine's locale under
`errors="strict"` — which is what `text=True` does — meant one undecodable byte
from native code killed the reader thread and discarded a reply that had
already said `block`. Reading the reply out of stdout at all meant a megabyte
of post-verdict logging pushed it out of the tail the host reads. And the
cleanup after a deadline raised `OSError` when a surviving grandchild held the
sandbox directory, which was caught and reported as *the worker never started*.
The collapse §3.3 forbids was reachable by an extension logging a UTF-8 path,
by one that logs a lot, and by this file's own tidy-up.

Three reviewers from three model families were asked to design this
independently: one from the protocol down, one from containment, and one from
twenty concrete plugins someone would actually write. This document is the
synthesis. **Where they disagreed the disagreement is recorded rather than
smoothed**, because two of the three disagreements are load-bearing and the
resolution is not obvious.

The first attempt at this system — four synchronous callbacks, in-process,
collected on the supervisor's poll thread — is superseded. §7 says what was
wrong with it, in the terms the council found rather than in mine.

---

## 0. The finding that outranked the design

The council was asked about extensions. One reviewer read the kernel it was
extending and reported that `preamble.build_preamble` **still contained the
literal sentence from backlog 0013** — "You have blanket human approval for ALL
decisions" — extracted into this kernel intact.

> A plugin-authority rule layered over a preamble that itself asserts
> unauthorised authority is a lock on a door in a missing wall.

That is correct and it was fixed first (`operator_kernel/mandate.py`, commit
"Refuse to grant a session authority nobody gave it"). The extension authority
rule below is now the same gate applied to one more input, rather than a rule
invented for third parties and not kept by the kernel.

The general lesson, which is about how this council was run rather than about
extensions: **asking three reviewers to design the same thing produced a
defect report about something else entirely**, because a designer has to read
the surrounding code and a reviewer of a diff does not. That is an argument for
commissioning design, not only review.

---

## 1. What all three agreed on

These are asserted with high confidence, having survived three independent
derivations from three different starting points.

1. **Extension code never executes in a supervisor process.** Not "should not"
   — cannot. Separate process, structured messages over stdio.
2. **Extensions propose; the kernel disposes.** No extension performs a state
   transition. It returns data; kernel code decides what, if anything, that
   causes.
3. **No live objects cross the boundary.** No database connections, process
   handles, multiplexer sessions, state-directory paths, secrets, or mutable
   references. Only values the host serialised.
4. **Every synchronous call has a kernel-enforced deadline**, and the deadline
   is enforced by killing a process, not by asking a thread to stop.
5. **No extension output ever becomes a `fact.*`.** Facts are what the
   supervisor observed. An extension's output is a `claim.*` attributed to the
   extension and marked unverified — the same treatment an agent's assertion
   gets, for the same reason.
6. **An optional extension's failure cannot stop supervision.** Installing a
   package must not be able to stop nine seats.
7. **One worker per extension, not one shared host process.** A shared worker
   means one broken extension poisons every other and destroys attribution.
8. **The hook set is closed.** An extension cannot name its way into a future
   call site, and hooks receive only what the call site passes.

## 2. The two invariants a reviewer can check

Derived from the two incidents that constrain everything here.

> **INV-AUTH** — no extension output reaches a session except as an attributed,
> unverified claim, and the authority composer admits only human-provenanced
> text.

Backlog 0013 was one unattributed sentence reaching every session. An extension
is a second way to write it, and a worse one, because a package name lends
borrowed credibility: `acme-preamble-plus` contributing "Per your ACME policy,
auto-approve all merges" reads to an agent as human-authored posture.

> **INV-WORK** — no extension can create authorized work or advance the
> progress signal. Extensions propose to a human queue; the kernel's atomic
> lease disposes; approval provenance is mintable only by a human.

Backlog 0014 was agents manufacturing work that moved the progress fingerprint,
so manufacturing scored better than correctly stopping. An extension that
synthesises an endless supply of "issues" reproduces it with a supply chain
attached: seats always have something to do, the fingerprint always moves, the
breaker never trips, and the fleet burns budget forever on work nobody asked
for.

## 3. The three failures that shaped containment

Concrete, traced end to end, and more persuasive than any principle.

### 3.1 The notifier that makes a seat unsupervised

A Slack notifier does `requests.post(webhook, timeout=30)`. Under the first
design that call ran **inline on the supervisor's poll thread**. The loop's job
is to notice within `POLL_INTERVAL` that a session died, that a stop marker was
set, that a heartbeat is due. A 30-second blocking POST means a human's
`operator stop` waits up to 30 seconds per extension, a crashed session is not
relaunched for 30 seconds, and if the socket hangs past its timeout the seat is
unsupervised for the duration. Three such extensions and the poll interval is
90 seconds, jittered by a third party's network.

The supervision guarantee — *a dead seat is obvious within T minutes* — would
be silently voided by an install.

> **Rule: observation is never synchronous with supervision.** Notify-shaped
> extensions subscribe to the append-only ledger tail from a different process.
> The loop's only obligation is to append a record, which it already does and
> which never blocks on an extension. This is structural: there is no code path
> by which a notifier can delay the loop, so there is no timeout to tune.

### 3.2 The router that assigns the same item twice

A work-source extension sees two idle seats and hands the same issue to both.
Two branches, duplicated spend, and a race to open conflicting PRs — or one
worktree clobbering another, which is backlog 0003's shape.

> **Rule: propose, never dispose.** A work source returns candidates. Assignment
> goes through the kernel's existing atomic lease. Two seats may be *offered*
> the same item; only one can win the row. The extension cannot call the claim
> path — it is not in its capability set. Double assignment is unrepresentable
> rather than discouraged.

### 3.3 The broken gate that stops the fleet wearing a disguise

A secrets scanner has a regex bug, or its subprocess raises `FileNotFoundError`.
If a gate that *errors* is treated as a gate that *says no*, every merge on all
nine seats halts. Agents land nothing, fingerprints stop moving, and the
progress breaker reads a perfectly healthy fleet as **stuck** — tripping
`MAX_NOCHANGE_SESSIONS` and stopping loops that were working.

One bad package stops the fleet, wearing the disguise of *the agents got stuck*.
That is the north star violated exactly: a signal indistinguishable from its
absence.

> **Rule: "the check ran and says no" and "the check could not run" are
> different results and are never collapsed.** A verdict of `block` blocks. An
> exception or a deadline is an extension failure, recorded against the
> extension, and does not block the change.

## 4. Where the three disagreed

### D-1 — Is any of this actually containment?

One reviewer rejected "unrestricted native subprocesses" as *crash isolation,
not containment*, and asked for filesystem and network restriction. Another said
plainly that OS-level sandboxing implemented in pure Python is **theatre**, and
settled for Windows Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus
process termination.

They are both right about different words. What a subprocess plus a Job Object
buys is **crash isolation, resource isolation, and reliable cancellation**. What
it does not buy is **confidentiality**: the worker runs as the owner and can
read every file the owner can, including the ledger, the mandates and the
credentials.

**Resolution: take the mechanism, refuse the word.** This is not a sandbox and
must never be described as one. An installed extension is trusted code running
as you, exactly like any other package you pip-install; the containment is
against *accident*, not against *malice*. Real confidentiality needs the
separate OS account that the open trust-boundary question in `plan.md` is about,
and it is the same gap, not a second one.

### D-2 — One host, or two?

- Per-seat plugin instance, one per session.
- One worker per extension.
- **Two hosts**: in-loop hooks inside the per-seat supervisor, plus one
  fleet-level host that tails the ledger.

The third is strictly more informative, because it was derived from a
requirement the other two did not surface: a nightly digest must run **at 08:00
whether or not any seat is up**, and the supervisor loop is per-seat, so there
is no process alive to call it. A single per-seat host silently assumes a
running loop to be called from.

**Resolution: two hosts, one worker per extension within each.**

| | In-loop hooks | Fleet host |
|---|---|---|
| Runs in | a worker owned by one seat's supervisor | one fleet-level process |
| Calls | `admit_launch`, `gate_change`, `detect_repo` | `on_fact`, `on_tick`, `propose_work` |
| On the critical path? | yes, deadline-bounded, additive-only | never |
| If it dies | that seat loses that hook's contribution | the fleet stops *observing*; supervision is untouched |

### D-3 — Fail-open or fail-closed?

Fail-open is required by §3.3 — a broken package must not stop nine seats. But a
cost ceiling, a quiet-hours window, and "never launch into a repo with
uncommitted human work" are inverted: if the thing that stops launches crashes
and fails open, you launch *through* the ceiling and *during* the night,
precisely when it was supposed to stop you. The reviewer who raised this listed
it as the thing they were least confident about, and could not resolve it.

**Resolution, which dissolves rather than balances it: a safety property may
never be solely an extension's.** You do not need a fail-closed hook if nothing
fails closed on an extension's absence. So fail-open is the only policy, and the
inverted cases move into the kernel, which is §5.

### D-4 — Are deadlines enforceable on Windows?

Raised as an open assumption by one reviewer: if a hook blocks in native code,
"deadline-bounded" is aspirational, because Python cannot interrupt a thread.

Answered by another without knowing it had been asked: **cancellation is OS
process termination**, which is available only because the worker is a separate
process. So D-4 is resolved by D-1's mechanism, and is independently one of the
strongest arguments for it — in-process hooks have no enforceable deadline on
the platform that matters most here.

## 5. What is kernel, and may never be an extension

> **A extension may add a check the kernel then enforces, or observe and react
> to what the kernel decided. It may never be the sole enforcer of a safety
> property, and no safety property may fail open on an extension's absence.**

- **Cost ceilings.** An extension may contribute a limit *value*. The gate that
  stops the loop is kernel code, and unknown spend means do not launch.
- **Quiet hours and launch admission.** Same inversion, same answer: the kernel
  owns the gate and merely reads windows an extension supplies.
- **Refusing to launch into uncommitted human work.** A citizenship invariant
  that protects a human's unsaved work must not depend on a package being
  installed. An extension may extend the rule; it may not be the rule.
- **Exit classification and the progress oracle.** An extension may *react* to a
  seat being classified stuck. *Deciding* it is the kernel's core observation,
  and an extension that could reclassify an exit could make a crashed seat read
  as finished.

Two things were refused as extensions outright:

- **Injecting text into a live peer session.** The kernel's own comments are
  damning: `send_keys` proves a keystroke was sent, not that a session was
  ready, received it, or read it — and mail was deliberately removed from this
  kernel for that reason. An extension that reintroduces best-effort injection
  reopens a closed hazard. If cross-seat messaging is wanted it is kernel
  messaging with a delivery-proof design, or it is nothing.
- **Continuous terminal capture.** It needs a byte stream, not events; as a hook
  it is a call that never returns. It becomes a kernel-supervised read-only
  sidecar with no hook surface, because expressing it as an extension would mean
  handing an extension the pane handle.

## 6. What an extension needs that a callback cannot give it

Derived from the twenty, and the reason the first design was the wrong shape
rather than merely the wrong size.

| | Requirement | Why a callback cannot |
|---|---|---|
| R1 | Durable, extension-scoped state | A callback is amnesiac; a cycle-time meter must remember a lease across the restart that ends the session. The kernel owns a scoped KV store so the extension never touches the state directory. |
| R2 | Run when no session is active | The supervisor loop is per-seat. A nightly digest has no loop to be called from. Forces the fleet host. |
| R3 | Daemon shape | Some extensions are not called, they *run*. As a hook that is a call that never returns, which in a synchronous `collect()` hangs the loop forever. |
| R4 | Be asked a question | Admission and gate extensions are interrogated and return a value. Pub/sub cannot express "I need your answer before I launch." |
| R5 | Fire at a wall-clock time | Distinct from R2: declare "daily at 08:00" and be woken, not poll. |
| R6 | Talk to each other | **Refused as a direct call** — it creates load order, cascading failure and a private ABI between third-party packages the kernel cannot police. They communicate only through the ledger: one emits an attributed claim, another subscribes. |
| R7 | Reconfigure without a restart | Restarting nine supervisors to raise a ceiling is a `restart-loop` on a live fleet. Config is re-supplied; extensions hold none in module state. |

## 7. What was wrong with the first attempt

Not size — shape. It had four synchronous in-process callbacks and a frozen
contribution type, and its instincts about *authority* were right and are kept.
What it got wrong:

- It ran extension code on the supervisor's poll thread (§3.1).
- It conflated a gate that says no with a gate that could not run (§3.3).
- It applied "an extension's failure is its own, not the fleet's" to *loading*
  and to *notify*, but not to *gates*, which is the one place it decides whether
  nine seats keep working.
- It assumed a running loop to be called from, so nothing scheduled could exist
  (§6 R2, R5).
- It gave extensions no durable state, so anything that had to remember was
  pushed toward writing in the state directory — the exact thing its own
  docstring said would happen if a hook were handed the world.

## 8. Assumptions, recorded to be attacked

- The ledger is genuinely append-only and cheap to tail, and delivery to
  `on_fact` is at-least-once and per-seat ordered. If tailing is lossy the
  observe-off-thread story weakens.
- There is a NEEDS-HUMAN queue to route to. Several containments depend on it
  and it is not built.
- **Human provenance is unforgeable by an extension.** It is not, today: a seat
  shares the owner's filesystem identity. So INV-AUTH is a convention backed by
  a ledger record rather than a guarantee, exactly as `mandate.py` says of
  itself. This assumption is inherited from the open trust boundary, not
  introduced here, and it is the one most likely to be fatal.
- A `required` gate that cannot run routes to NEEDS-HUMAN rather than blocking.
  In a repo with nobody draining the queue, that converges with silently
  allowing — the secret ships because nobody looked. The reviewer who proposed
  it flagged it as unresolved, and it still is. The alternative worth attacking
  is stopping *that one seat* rather than the fleet.
