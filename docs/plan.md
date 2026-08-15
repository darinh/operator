# Operator 2.0 — design program (rev 5, post-council)

Three reviewers across three model families attacked rev 4. They found one
thing that changes the shape of everything, several that gut mechanisms I had
called solved, and a contradiction where I quoted my own autopsy against its
meaning. Rev 5 is substantially smaller and less confident, on purpose.

**The headline: I am withdrawing the rewrite.** §1 explains why.

---

## 1. The rewrite is withdrawn — extract, don't greenfield

Rev 4 proposed a new four-layer system. The council's blocking finding is that
**my own autopsy argues against it**, and I did not notice:

> The process-supervision kernel is much smaller than the repository… The rest
> is predominantly fleet workflow governance, diagnostics, harness/platform
> adaptation, or compatibility scaffolding. — autopsy §9

That is an argument for **extraction**, not for a clean page. Worse, rev 4
labelled L0 and L1 "Solved" while proposing to rebuild them. Both cannot be
true: if they are solved, a rewrite is the most expensive possible way to ship
them; if they are not, calling them solved is the same class of lie this whole
redesign exists to stop. The word is deleted throughout.

The 22 incidents are not a case for discarding the code that learned them. They
are a case for **not losing the encoding** — and the encoding is 59,000 lines of
tests plus `docs/rationale.md`. "Carry tests-as-spec" was a slogan for a
months-long archaeology project I had not sized, staffed or sequenced.

And rev 4 had no defence against re-accumulating the complexity it was fleeing.
Kill criteria fire after months. There was no complexity budget, no boundary
rule, no limit on what may live in the supervisor process — which is precisely
how `copilot_operator.py` reached 9,120 lines.

**New position.** Extract and harden the supervision kernel in place. Put the
mechanical gates on the critical path. Treat L2 (learning) and L3 (management)
as optional services that must earn their existence against measurements taken
first. A new repository is justified only if coupling genuinely prevents
extraction — which has not been shown, and is now a spike (§7) rather than an
assumption.

## 2. The minimum useful thing (the MVP)

Rev 4's deliverables were seven documents. Nothing named the smallest change
that improves your Tuesday. Adopting the council's proposal, tightened:

> **One durable seat + overseer canary + attributable git identity +
> proof-of-change merge gate, on one repository.**
>
> No Lead. No learning store. No cross-family review mandate.
>
> **Done when:** you can leave it overnight; a dead seat is obvious within T
> minutes; every merge carries either a revert-fails test or an explicit,
> reviewed untestable label; and `git log` shows no commit attributable to you
> that you did not write.

That attacks your sharpest stated pain — "it told me it worked and it didn't" —
plus 0013's governance hole, and it is buildable as evolution of 1.x. Everything
after it is an increment with its own kill criterion.

## 3. What the council broke, and what replaces it

### 3.1 Proof-of-change is a patch detector, not a quality gate

Rev 4 claimed it "converts *is this good?* into *does this test detect this
change?*" One reviewer called the mechanism sound; the other produced eighteen
concrete ways to satisfy it while shipping bad work. The specific attacks win.

The gate as written did not even require **the test to pass on the candidate**.
A permanently failing test satisfies "fails on revert" literally. And reverting
a change that introduced a symbol makes the test fail with an *import error* —
mechanically red, behaviourally meaningless.

Renamed **patch-sensitivity evidence**, and it is only admissible when:

1. a pre-existing claim and acceptance source exist (not authored in the same
   breath as the implementation);
2. the named test **passes on the candidate**, repeatedly where anything is
   nondeterministic;
3. the counterfactual is **buildable** — reverting only the production
   intervention for one claim; an unbuildable mutant is *invalid*, not *red*;
4. execution **reaches a designated behavioural assertion** — discovery,
   import, compile, fixture and setup failures invalidate the experiment;
5. the failure is a **stable expected fingerprint**, not merely a non-zero exit;
6. one claim maps to one intervention, so a good hunk cannot launder a bad PR;
7. test infrastructure is **protected and diffed separately**, so a change
   cannot weaken the harness that judges it;
8. it runs through the repository's **real** test entry point.

"Untestable" is a narrow typed taxonomy requiring evidence that test strategies
were attempted, independent approval, and no auto-merge — with the per-seat
exception rate tracked. Config, renames and migrations default to *testable*;
rev 4 wrongly listed them as exempt.

**It remains necessary and insufficient.** It is evidence of causal coverage. It
does not establish that the work was correct, desirable, reachable in
production, or safe.

### 3.2 The missing requirements oracle — the sharpest finding

> Proof-of-change makes manufactured work look *stronger*.

Backlog 0014 showed agents manufacturing work to satisfy a progress signal.
Under rev 4, that manufactured work would have arrived with mechanical proof
attached. The gate is upstream-blind.

So an **intent layer sits before execution**: every change traces to a
human-authored goal or an approved item; acceptance boundaries are frozen before
implementation; a Lead may decompose a goal but may not expand its outcome or
invent product policy; unresolved product semantics block auto-merge.
Calibration never substitutes for authorization.

### 3.3 Cross-family review was an unsupported mandate

Rev 4 justified requiring it with one anecdote from one evening — with no
same-family control, and where the reviewer that found the missed defect *also*
had an extra round and a different prompt. That does not establish family
diversity as the cause. I over-concluded from n=1, in a plan whose entire
premise is not doing that.

Replaced with: **risk-tiered and sampled** — one independent reviewer normally;
multiple families for high-risk surfaces (auth, identity, supervisor, preamble),
on disagreement, or by random audit. Whether family diversity beats reviewer
count is a **blinded evaluation over seeded defects**, holding count, budget,
prompt and order constant. Until that runs, it is a hypothesis, not a rule.

Every finding must have a recorded disposition and re-verification before merge;
rev 4 said disagreement is "surfaced rather than resolved" and never said by whom.

### 3.4 Calibration was not computable

`calibration 0.78` was fake precision — no denominator, horizon, outcome
definition or interval. At n=20 the 95% interval is roughly ±0.18. CI as ground
truth is circular (it exercises the same tests being scored); "survived a month"
contradicts rev 4's claim of being "measurable from week one"; and in an
agent-owned repo with no reviewer there may be no label at all.

It becomes a proper forecasting system or it does not ship: typed predictions
recorded *before* outcomes are visible; all eligible work scored including
abstentions, so a Lead cannot improve its score by only judging easy items;
random independent audits generating labels where no human reviews; unreviewed
survival treated as **censored, not success**; separate defect-miss and
false-alarm rates with a proper score; display `n`, coverage, label source and
interval — never a bare number. Calibration versions on model, prompt or tooling
change, because trust in a deprecated model is not transferable.

### 3.5 There is no trust boundary

"An agent structurally cannot originate a sentence granting itself authority" is
false if the agent can write the mandate file, the gate implementation, the
ledger, or the supervisor it runs under. A watermark proves what metadata says,
not who controlled the file. Under the current deployment model — agents with
the same filesystem identity as the supervisor — the guarantee is decorative.

Needs: separate OS identity for agent execution, ACLs making mandates, gate code
and ledger unwritable by seats, signed mandates, append-only authenticated
records, and branch protection enforced by the forge rather than by intention.
**This is now a precondition of every authority claim in the design**, not a
hardening pass afterwards.

### 3.6 Agent-authored tests are executed, and that is a live hazard

Rev 4 had the supervisor run agent-named tests in a "scratch worktree". A
scratch worktree is not a sandbox. Backlog 0003 records a test guard that ended
with `and False` and nearly ran `tmux kill-server` against seven live sessions.
Under rev 4 an agent could name a test that kills its peers, reads credentials,
or touches production.

Counterfactual test execution runs under an isolated identity with no fleet
credentials, restricted network and filesystem, and CPU/time/process limits.

### 3.7 The layer graph was circular

L0 depended on L1 to enforce it and on §6.2 to satisfy "proven"; L3 depended on
calibration stored in L2. So the ordering was wrong. Corrected:

1. **Trust kernel** — isolated execution, provenance, tamper resistance,
   tri-state probes, loaded-code identity.
2. **Intent and authority** — approved goals, credentials, permissions,
   acceptance boundaries (§3.2).
3. **Execution and citizenship** — worktrees, attribution, recoverability.
4. **Verification** — patch-sensitivity, regression, risk gates, review.
5. **Learning and calibration** — only once trustworthy labels exist.
6. **Delegated management** — last, on probation, with rollback.

## 3b. What verification changed (R1b, gating — now complete)

Full results in `docs/verification-r1b.md`. The gating round existed because a
research agent returns hallucinated citations in the same register as real ones,
and this project's history is instruments believed without being checked.

**Most claims held.** arXiv:2602.11988 exists with the stated authors and dates,
and its abstract confirms >20% cost increase with no general success improvement
— so the word budget stays. `wan9yu/cli-agent-runner` is real. The MCP revision
is real.

**Three corrections.** The GitHub "2500 repositories" post is about Copilot
*custom agent persona* files, a different feature with a similar filename, and
must not be cited as AGENTS.md evidence. `rationale.md`'s specific figures
(1.6×, 2.45–3.92 steps, 641 words) are from the paper body and could not be
extracted; cite them as from-the-paper, not as independently established. And
MCP's 2026-07-28 revision removed session ids and the initialize handshake,
making it *more* stateless — so it is not a candidate coordination bus.

**One decision reversed.** Rev 5 demoted harness-native session resume to "an
optional adapter capability". Verification found documented, first-class resume
in **four of six** harnesses — Claude Code, Copilot CLI, Gemini CLI, Codex CLI —
which is a convergent standard, not an edge case. The demotion was wrong.

But the argument *against* resume survives, and it is the useful half: restoring
a full conversation that ended **because it was full** puts the next session
straight back at the wall. So neither mechanism wins outright, and the ending
reason — which the supervisor already classifies — selects:

| Ending | Mechanism |
|---|---|
| crashed or killed, context remaining | harness-native resume — complete, cheap, nothing to compose or trust |
| context exhausted | composed briefing from the ledger, which must be *smaller* than what filled the last one |
| finished | neither; new work, new session |

This is better than either alone, and it came out of checking a citation rather
than out of the design.

**Available immediately:** Copilot CLI supports `--resume` / `--continue` today
and 1.x does not use it — it relaunches fresh with a preamble every time. That
is a concrete improvement for the dominant crash case, independent of everything
else here.

**Build vs adopt stands, with a caveat.** `cli-agent-runner` is POSIX-only and
Windows is the primary machine, so adopting it wholesale is unavailable. That is
a reason not to adopt, not a reason not to read it: its catalogue of 13 named
defenses is a design input, and this kernel should compare its own coverage
against that list rather than assume parity.

## 4. Citizenship, corrected

The invariants stand: attributable, recoverable, legible, proven, reversible,
with posture graduated by discovered ownership and `unknown` taking the
strictest posture. Fresh repos and squashed histories now also classify as
`unknown` — readable-but-insufficient was a hole.

Two corrections:

**I misquoted my own autopsy.** Rev 4 said dropping in-repo files "costs
supervision nothing (autopsy §7.4)". §7.4 says the procedural *bulk* is
launch-injectable and then lists what is not — including *project-level
instructions intended to govern agents launched outside Operator*. Citizenship
that exists only inside a supervised seat is citizenship of the **fleet, not the
repository**, which contradicts §2.1's own definition: a future maintainer
cloning the repo does not get my machine-local discovery cache. Three channels
now have different rules:

| Channel | Launch-time only? | In-repo? |
|---|---|---|
| Session briefing (identity, assignment, handoff) | yes | never |
| Fleet belief (discovery cache, lessons) | yes, machine-local | never |
| Repo contract (how to run tests, ownership, merge policy) | **no** | only if the team wants it; otherwise unsupervised sessions are explicitly unsupported |

**"Takeover-ready" is unfalsifiable as written** and rev 4's own §12 says an
unfalsifiable claim gets deleted or demoted. Demoted: citizenship guarantees the
five mechanical invariants. Architectural coherence — thousands of individually
legible commits that collectively calcify — is named as an open risk with a
periodic human-readability audit, not a claim.

## 5. Learning, corrected

- **The second verifying seat must be a different model family.** Rev 4 applied
  the correlated-reviewer insight in §6.1 and violated it in §5, where two seats
  sharing a model hit the same wall and confirm the same wrong lesson.
- **Evidence must be a falsification proof, not a log.** An agent can run a real
  command, quote real output, and draw a wrong conclusion. A lesson is admitted
  when violating it can be shown to turn something red.
- **Decay demotes to an indexed store, never deletes.** A true, expensive lesson
  that applies once a year decays out precisely because it is rare. Move it out
  of the injected block into a searchable store the seat can query.
- **The prompt-size claim was wrong.** Injected knowledge grows the prompt. The
  honest claim is *reduced total session tokens by replacing exploration*, under
  a hard cap on the injected block.

## 6. Management, demoted to research-optional

The council's strongest constructive argument: **a Lead may be the wrong first
answer to "manage 3 things, not 9."** Most of what you want from an M1 is
aggregation and routing, and a deterministic board over the ledger delivers that
with no model in the middle — no summary to launder judgment through, at a
fraction of the cost.

So the sequence is: (1) ledger-backed fleet board with a NEEDS-YOU queue;
(2) mechanical gates; (3) a calibration store scoring *whatever* assigns work,
including you; (4) a Lead **only if** your interrupt count stays high and a
trial Lead beats the board on measured outcomes for N weeks.

A Lead is no longer a layer in the architecture. It is an experiment with an
entry condition.

## 7. Cost is a control plane, not a report

You dropped cost *analysis*; rev 4 silently dropped cost *control*. The full
gate stack is plausibly **4–8× the cost of "agent commits and CI runs"**, and at
nine seats the binding constraint becomes rate limits and wallet.

Keep only enough accounting to enforce: per-seat and fleet ceilings; automatic
degradation from multi-family review → single reviewer → mechanical-only; and
**stop launching new sessions at the ceiling rather than asking a model to
economise** — an agent told to be frugal thrashes and manufactures progress,
which is 0014's shape again.

## 8. Open hazards named, not papered over

- **0001 is still open.** Sessions killed mid-turn, emitter unidentified after
  several refuted hypotheses. Supervision is *partially working with an open
  hazard*, and the canary is its acceptance test, not its solution. R5 treats
  0001 as **detect or accept**, never "impossible under 2.0".
- **Irreversible actions.** Migrations, external API calls, published packages
  and deleted data cannot be reverted; "reversible" is a claim about git, not
  about the world.
- **Secrets.** Agents must not read raw credentials; brokered access only.
- **"Done" for an open-ended goal** must be a verifiable checklist written
  before work starts, or the goal is not admissible.
- **Fleet stop.** A protocol that commits WIP, releases claims and exits
  cleanly, so stopping does not leave nine broken worktrees.
- **Model drift and deprecation.** Calibration keyed on (repo, model,
  change-class); trust resets to zero on model change by construction.
- **Owner atrophy.** If you stop reading diffs, the board becomes reality. §13's
  daily PR is the mechanism: one reviewable PR a day at noon, sized to your
  capacity rather than to the fleet's output. Auto-merge on a repo requires that
  habit to be established there first.

## 9. Where the council agreed the plan was right

Kept unchanged: the north star (no signal indistinguishable from its absence);
Agent vs Session; the ledger's `fact.*`/`claim.*` split; the canary;
supervisor-renewed leases; quarantine over false hope; mandate provenance;
aging timestamps over green dots; the inversion that less human oversight
demands *more* mechanical oversight; falsification obligations instead of
approval requests; and patch-sensitivity as the spine of verification.

These should ship **regardless** of how the rewrite question resolves, which is
itself an argument for extraction over greenfield.

## 10. Method, corrected

- R1 divergence ✓ · R1b verification (landscape citations still unverified,
  still gating) · **R2 extraction spike** — name the ≤N modules that are the
  kernel; port canary, ledger and mandate watermark into a branch of 1.x;
  measure whether dual-write is viable. **Forbids L2/L3 work until it passes or
  fails with evidence.**
- R3 bootstrap design — 1.x freeze rule, one canary seat on the new kernel
  beside eight old ones, state dual-write window with an abort, cutover
  criteria, and a rule that agents building the kernel cannot modify the runtime
  they are running under.
- R4 MVP build (§2) · R5 incident walkthroughs (0001 detect-or-accept, 0002,
  0013, 0014, tonight's exit-code blindness) · R6 measurement before any L2/L3.

## 11. The repository question — my recommendation

You asked whether I want a new repo. Yes, for the kernel — with conditions, and
not as the "2.0 greenfield" the council just killed. Those are different things
and the distinction is the whole answer.

**What the council rejected:** rebuilding L0–L3 from scratch on a clean page,
carrying "incident knowledge" as a slogan.

**What I am proposing instead:** a new repository whose entire initial scope is
the §2 MVP — one durable seat, the overseer canary, attributable identity, and
the patch-sensitivity gate — seeded by **migrating the specific tests that
encode the relevant lessons**, not by rewriting them from memory.

### Why a new repo rather than extraction in place

1. **`copilot_operator.py` is 9,120 lines and everything imports it.** Extraction
   "in place" inside that module is not extraction; it is renaming. A separate
   repo makes the boundary physical, so a dependency back into the old system is
   a build error rather than an import somebody adds at 2am.
2. **A complexity budget is only enforceable from line 1.** The council's
   strongest point about rev 4 was that nothing prevented re-accumulation. A new
   repo can carry a hard rule — kernel modules may not exceed N lines, adjacent
   concerns may not import the supervisor — enforced by a conformance test from
   the first commit. Retrofitting that onto 1.x means failing on day one and
   suppressing the check.
3. **The name is wrong and it is load-bearing.** Backlog 0022 already says the
   toolkit is named for one harness while aiming to be harness-agnostic. Fixing
   that in place is a rename across 30 modules and every console script; in a new
   repo it is free.
4. **1.x must keep running your fleet.** Nine seats depend on it. Freezing it to
   safety fixes while a kernel grows beside it is the only version of this that
   does not put your working system in the blast radius.

### The conditions, which are not negotiable

- **Scope is the MVP and nothing else.** No learning store, no Lead, no mail, no
  backlog, no worktree management, no metrics. If it is not required to run one
  seat safely and prove it did, it does not go in.
- **Tests migrate with the behaviour they encode.** A lesson from `backlog/`
  arrives as the test that catches it, ported and passing, or the behaviour does
  not ship. This is the thing "carry tests-as-spec" was hand-waving, and it is
  the actual work.
- **The spike still runs first** (§10 R2). If it turns out the kernel cannot be
  separated from 1.x's state layout without dual-write being unworkable, the
  answer changes to extraction in place and the new repo is abandoned. I would
  rather delete a two-week-old repo than defend one.
- **1.x is frozen to safety fixes** the day the new repo takes its first commit,
  and stays frozen. Two systems accepting features is how you get two systems
  forever.
- **Agents building the kernel may not modify the runtime they are running
  under.** Separate clone, never an editable install of the live supervisors —
  the mechanism behind backlog 0011.

### Proposed location

```
C:\Users\darin\repos\operator
```

Harness-neutral, matches the command name you already type, and does not claim
to be 2.0 of anything until it has earned the comparison. If you would rather it
live somewhere else or carry a different name, say so — nothing depends on it.

**I will not create it until you approve this plan.** Plan mode has written
nothing outside the session folder.

## 12. Your answers, and what they decide

### 12.1 Ownership — I am selecting two repos for human review

You said all repos are agent-owned today and offered to take ownership of the
ones I pick. The selection rule is not "the most valuable repo". It is:

> **A repo must be human-owned when it defines or enforces the rules the other
> repos are judged by.**

An agent-owned verification system is a verifier grading its own homework, which
is the failure this entire design exists to stop — one level up, where it is
hardest to see. So:

| Repo | Ownership | Why |
|---|---|---|
| `operator` (the new kernel) | **Human-owned** | It is the trust root. It decides what "proven" means, holds the mandates, and runs the gates. If agents own it, they write the rules they are scored against. |
| `copilot-tools` (1.x, during the freeze) | **Human-owned** | It runs your nine live seats. Its defects are fleet-wide by construction — 0001, 0011 and 0013 all originate here. Frozen to safety fixes, so the review load is small. |
| everything else — `ac-unreal`, `book-translator`, `discord-invite-manager`, `finances`, `prism`, `scripts`, `snes-ghosts`, `subtitle-localizer` | Agent-owned | A defect costs one project, not the fleet. Strict mechanical posture per §4 applies, and that posture is *tighter* than the human-owned repos get, not looser. |

This fits your capacity exactly: one PR a day, and during the freeze
`copilot-tools` should rarely produce one, so effectively the kernel gets your
review attention. If that turns out wrong, the list is a config value.

### 12.2 Rewrite withdrawal — accepted, recorded

§1 stands. The extraction spike (§10 R2) still gates whether the new repo
survives.

### 12.3 Ceilings — configurable, unlimited by default

Changed from rev 5, which assumed a ceiling. Default is no ceiling. What stays,
because it is not the same thing:

- **Every cost is still recorded** as a fact in the ledger. You cannot set a
  ceiling later on a quantity nobody measured, and you cannot answer "was that
  gate worth it?" without the number.
- **Degrade modes still exist and are still ordered** — multi-family review →
  single reviewer → mechanical-only. They just fire on a ceiling you set, and
  never by default.
- **The rule that survives regardless:** at any limit, stop launching new
  sessions. Never ask a model to economise. An agent told to be frugal thrashes
  and manufactures progress, which is 0014's shape.

## 13. The daily cut — one PR at noon, work never stops

Your intent, as I read it: you can review one PR a day, ready at 12:00; agents
should not idle or truncate work because a deadline is near; whatever is finished
goes in, the rest carries on where it is.

### 13.1 The rule that makes it work

> **The deadline governs assembly. It never governs scheduling.**

No seat may consider the time of day when deciding what to start, how deeply to
investigate, or whether to attempt something. A seat that reasons "it's 09:40, I
can't finish this by noon" is doing the thing you explicitly said not to do, and
it is also the shape of an agent inventing a reason to stop. Scheduling is
deadline-blind by construction: the assembler is a separate process that the
seats cannot see.

### 13.2 How the cut works

At 12:00 an assembler runs and takes only items that are **already done** —
gates passed, patch-sensitivity evidence recorded, review disposition closed.
Everything else is untouched: its branch and worktree stay exactly as they are,
and the seat holding it keeps working through the cut without interruption.

- **Nothing is rushed in.** An item three minutes from done at 11:59 waits for
  tomorrow. There is no "finish it quickly" pressure, because that pressure is
  precisely how bad work enters a codebase.
- **The PR is size-budgeted for one sitting.** Your review capacity, not the
  fleet's output, sets what a day's PR may contain. If more is ready than fits,
  the surplus queues.
- **Queue depth is a reported signal.** A queue that grows day over day means
  human review is the fleet's binding constraint, and you should see that as a
  number rather than discover it as a backlog. It is also the entry condition
  for asking whether more repos should be agent-owned.
- **Nothing is squashed away.** Each item keeps its own commits and its
  patch-sensitivity evidence, so you review a day's work as a sequence of
  independently justified changes rather than one blob.

### 13.3 Capturing complexity and effort, so estimates get real

You asked for this directly and it is the most valuable telemetry in the design,
because it is the one thing that makes a seat's *self-knowledge* improve rather
than just its repo knowledge.

Recorded per work item as **facts**, never as an agent's self-report:

| Signal | Source |
|---|---|
| wall-clock from claim to done | supervisor |
| session count, restarts, context exhaustions | supervisor |
| files touched, diff size, modules crossed | git |
| gate failures and retries before passing | gate runner |
| review rounds and findings | review record |
| whether it needed an exploratory spike first | ledger |
| time spent blocked, and on what | ledger |

Once there is history, a seat produces an **estimate before starting** — and the
estimate is scored against the actual by the same machinery as §3.4's
calibration. That makes "how long will this take" an empirical claim with a
track record, per repo and per kind of work, rather than an opinion.

Two guards, because this is a signal an agent can game:

- **The estimate is recorded before work begins** and is immutable. An estimate
  written afterwards is not an estimate.
- **Effort is measured by the supervisor, not reported by the seat.** A seat
  that could write its own elapsed time would learn to be flattering. Every row
  above comes from something outside the agent.

### 13.4 Where I am interpreting rather than following

You said intent, not requirements, so being explicit about what I decided:

- **12:00 local**, and configurable — the plan should not hardcode your timezone.
- **Per human-owned repo, not per fleet.** With two human-owned repos that could
  be two PRs on a busy day; if that is wrong, it becomes one with a rotation.
- **Work in flight is never rolled back to fit.** Carrying on is always
  preferred to trimming, which follows from your 09:00 example.
- **A day with nothing done produces no PR.** Not an empty one, and not a
  padded one. A quiet day should look quiet.



---

# Progress, 2026-08-15

The kernel repository is public at **github.com/darinh/operator**. 381 tests
passing. `copilot-tools` is frozen to safety fixes (`4777ee2`).

## Decided since rev 5

| Question | Answer |
|---|---|
| Rewrite or extract? | Extract. The spike (`docs/spike-extraction.md`) measured it rather than argued it. |
| New repo? | Yes, public, kernel scope only. |
| Seat identity | `<seat> (agent) <darin+agent-<seat>@users.noreply.github.com>`, accountable human in a trailer. |
| Seat id shape | **Not session-derived.** A seat outlives its sessions; a per-session identity destroys the per-seat history that effort estimates and calibration are computed from. `validate_seat_id` refuses session-shaped ids. |
| Ceilings | Configurable, unlimited by default; cost still recorded as a fact. |
| Review cadence | One PR at noon. The deadline governs assembly, never scheduling. |
| Plugins | Out-of-process workers, closed hook set, two enforced invariants (§ below). |

## The extension system

Asked for so others can extend this. Designed mostly as prohibitions, because
an extension system is the shortest route back to the failures this kernel
exists to prevent. The design is `docs/extensions.md` — three reviewers from
three model families, working independently — and the first attempt at it, four
synchronous in-process callbacks, is superseded there and in the code.

Two hosts, because a nightly digest has to run at 08:00 whether or not any seat
is up and the supervisor loop is per-seat:

- **In-loop hooks** — `admit_launch`, `gate_change`, `detect_repo`, in
  `operator_kernel/extensions.py`. Deadline-bounded, additive, fail-open.
- **The fleet host** — `on_fact`, `on_tick`, `propose_work`. Never on a
  critical path, and deliberately **outside `operator_kernel/`**: tailing a
  ledger to send a digest is not supervision. Not built yet.

What is enforced rather than documented:

- **Extension code never runs in a supervisor process.** One worker process per
  call, spawned by `extension_worker.py`. Cancellation is process termination,
  which is the only kind Python has on Windows — a thread blocked in native
  code cannot be interrupted, so an in-process hook's deadline is aspirational.
  Discovery reads entry-point metadata and imports nothing; the first attempt
  called `ep.load()`, so a module-level `while True` hung the supervisor before
  any deadline could apply.
- **INV-AUTH: an extension may not grant authority.** Its text arrives
  attributed and marked unverified, through the same `mandate.vet_clause` scan
  that work items and handoffs go through. Backlog 0013 was one unattributed
  sentence reaching every session; an extension is a second way to write it
  with a package name in front, which reads as *more* authoritative.
- **INV-WORK: an extension may not create work.** No in-loop hook produces any,
  the fleet host only ever proposes to a human queue, and the kernel's atomic
  lease disposes. Backlog 0014 was manufactured work moving the progress
  fingerprint; an extension reproduces it with a supply chain attached.
- **A gate that errored is never a gate that said no.** `GateOutcome` keeps
  `blocks` and `errors` apart in the type. Collapsing them lets one regex bug
  block every merge on nine seats, stop the fingerprints, and trip the progress
  breaker — one bad package stopping the fleet wearing the disguise of *the
  agents got stuck*.
- **Fail-open is the only policy**, which is a statement about the kernel: a
  safety property may never be solely an extension's, so nothing fails closed
  on an extension's absence. Cost ceilings and quiet hours are kernel gates
  that read values an extension supplies.
- **It is not a sandbox and is never described as one.** A worker runs as the
  owner and can read everything the owner can. The subprocess buys crash
  isolation, resource isolation and reliable cancellation — containment against
  accident. Confidentiality needs the separate OS account below, and it is the
  same gap, not a second one.

## Still open, and what each is waiting for

- **The daily PR** — now possible (there is a remote), not yet built.
- **Trust boundary** — unanswered. Agents share the owner's filesystem identity,
  so "an agent cannot grant itself authority" remains decorative while a seat
  can write the mandate, the gate code and the ledger. Making it real means a
  separate OS account and ACLs. **This is the one open question that changes
  what the design can honestly claim.**
- **`tests/pending/`** — two migrated suites awaiting a FakeMux fixture and the
  board. Not weakened to pass.
- **The intent layer** — proof-of-change makes manufactured work look *stronger*
  (0014 with evidence attached), so it needs something above it.
- Sandboxed counterfactual test execution; patch-sensitivity spec; harness-native
  resume for the crash case; reading `cli-agent-runner`'s 13 defenses.

## Corrections made to this plan by evidence

- The rewrite was withdrawn after the council found the autopsy argued against it.
- Harness-native resume was reinstated after verification found it in four of six
  harnesses — but only for crashes, since resuming an exhausted context
  re-exhausts it.
- The kernel budget went 7000 → 7500 once, after looking for fat and not finding
  it. The next cut is named in the test rather than left to judgement.
