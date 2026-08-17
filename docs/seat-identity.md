# Seat identity — a design, not yet a decision

**Status: built, and the argument in §4 is still open.** `operator_memory/` is
the substrate, `operator-seat` is the command, and `build_preamble` mentions it
to a seat that has entries. What is *not* settled is whether a seat should
remember at all — §4 is the case against, it was written before the code and it
has not been answered by building it. Backlog **0037** tracks the question.

The request, in the owner's words:

> "I want agents who really are a specific identity / seat and not just a
> session with a handoff."

---

## 1. What a seat is today

Read out of the code rather than inferred.

* **`instance.py`** — `Instance(display_name)` is a name plus marker and state
  files: pid, loop pid, session number, restart/stop/detach markers, an
  ownership token, and two failure streaks. Every field is mechanical and every
  field is about the *current* run.
* **`preamble.py::build_preamble`** — what a launching session is told about
  itself is two clauses: `"(4) You are the @{agent} agent"` and `"(5) Operator
  instance: {display_name}"`. Everything else is mechanism (it relaunches,
  nobody is watching) plus the authority clause.
* **The handoff is a baton, and the preamble says so**: *"The reader is the one
  who deletes a handoff, so delete it once you have taken in its contents."*

So a seat's durable self is a name, plus one file that the next session is
instructed to consume and delete. On this machine every project directory holds
exactly one handoff file. `prism` is at session #226 and has one.

## 2. The measurement that decides the design

The obvious proposal — *make the handoff richer, keep more of it* — is dead on
arrival, and it takes a five-minute measurement of `~/.operator/trace.jsonl` to
see why.

Of **1,110** recorded session endings:

| How it ended | Count | Share |
| --- | ---: | ---: |
| `restart` — the handoff path | 113 | **10.2%** |
| no marker set at all | 997 | **89.8%** |

Per seat, the share that did *not* take the handoff path:

| Seat | Unexplained | Seat | Unexplained |
| --- | ---: | --- | ---: |
| book-translator | 98.1% | discord-invite-manager | 93.8% |
| finances | 97.1% | copilot-tools | 92.7% |
| prism | 95.8% | ac-unreal | 33.3% |
| snes-ghosts | 95.4% | scripts | 17.0% |

`record_session_exit` is careful that "unexplained" means *no stop, detach or
restart marker was set* and is **not** evidence of a crash. That caveat does not
rescue the handoff, because the inference runs the other way: the `handoff`
command sets the restart marker, so an ending with no restart marker is an
ending that wrote no handoff. Whatever those 997 endings were, they left
nothing behind.

> **The seat's only memory is written on the rare path.** For most seats it is
> written once in twenty endings. A design that persists knowledge *at the end
> of a session* inherits a 90% loss rate, and it loses precisely the sessions
> that ended badly — which are the ones whose experience was worth keeping.

Two consequences, and they are the whole design:

1. **Memory must accrete during the session, not at the end.** Every write must
   be durable the moment it is made.
2. **The handoff should stay exactly what it is** — a baton for the next
   session, small and consumed. It is not the substrate for identity and
   widening it would make the 90% loss worse, not better.

## 3. What identity is actually made of

"Persistent memory" conflates three things that have different lifetimes,
different risks and different homes.

| Kind | Question it answers | Where it lives today |
| --- | --- | --- |
| **Episodic** | what happened, when | `trace.jsonl`; the Copilot session store. Mechanical, complete, uncurated. |
| **Semantic** | what I concluded about this system | handoff "Context" — lost on 90% of endings |
| **Dispositional** | what I am like, what I am habitually wrong about | **nowhere at all** |

Episodic is largely solved and nobody reads it. The request is for the second
and third, and the third is the one that has no home whatsoever.

Note also that `store_memory` — the Copilot CLI's own facility — has scopes
`repository` and `user` and **no seat scope**. Two seats on one repository share
a memory; a seat spanning two repositories has none of its own. It is a real
store and it is keyed on the wrong axis for this.

## 4. The objection that has to be answered first

**A seat that remembers is a seat that can be permanently wrong.**

Amnesia is not purely a defect here. A session that has forgotten everything is
forced to re-derive from the repository, and the repository is ground truth. A
seat carrying its own conclusions from session 12 into session 500 will keep
re-reading a belief that may have been wrong when it was written or may have
been made false by the intervening 488 sessions — and it will read it in the
most credible voice available, its own.

This is backlog 0013 with a better disguise. 0013 was one unattributed sentence
reaching every session and later being quoted back to the owner as his own
instruction. A seat journal is a *machine for generating* such sentences, signed
by the one author the agent has no reason to doubt.

So the invariant is not optional and it is not satisfied by good intentions:

> **INV-SELF** — a seat's recollection reaches a session as a dated, attributed,
> falsifiable *claim about the past*, never as a statement about the present.
> "In session 12 I concluded X" is admissible. "X is true" is not, and neither
> is anything a reader could mistake for it.

That is `extensions.claim_text` applied to the seat itself, and it is the same
treatment an extension's claim already gets. The repository stays the source of
truth; the journal is a witness, and witnesses are cross-examined.

## 5. Shape of the thing

**Substrate.** `~/.operator/projects/<guid>/journal/<instance>.jsonl` — a
sibling of `handoff/`, append-only, one JSON object per line. Append-only is
what makes a write durable at the moment it happens, which is requirement (1).
It is the same file discipline as `trace.jsonl` and can reuse `evidence._append`
rather than growing a second appender that drifts from the first.

**Writing.** The seat writes as it goes, not at the end:

```
operator remember --kind gotcha "rotation is a rename, so a tailer must
                                identify by (st_dev, st_ino), never by size"
```

Each entry carries `ts`, `instance`, `session`, `kind`, `text`, and
`verified: false`. Nothing may write `verified: true`; there is no spelling of
it, for the same reason a proposal has no spelling of `approved`.

`kind` is a closed set — `decision`, `gotcha`, `disposition`, `attempt` — so
recall can be selective and so an entry has to say what sort of claim it is
before it is allowed in.

**Recall.** This is the hard half, and there are three candidates:

| Option | Cost | Objection |
| --- | --- | --- |
| (a) inject into the preamble | kernel change; ~67 code lines of budget remain | every entry is now instruction-adjacent text; hardest INV-SELF case |
| (b) `operator recall` run by the session | one preamble clause | agents do not run commands nobody told them about — but the preamble already tells them to read the handoff, so this is one more clause |
| (c) a `detect_repo` extension | none to the kernel | **`detect_repo` has no call site** — this is the piece that would have to be built |

**(b) is what was built**, on the strength of one distinction: text the agent
*fetched* is evidence it went and got, while text sitting in its instructions is
posture. The distinction is not total — a determined reader can treat either as
authority — but it is real, it is the same distinction the extension design
draws between a claim and a clause, and it costs one sentence in the preamble
instead of a kernel subsystem.

The preamble clause is the one kernel change: `paths.project_journal_file` and
`paths.seat_has_journal` (one `stat`, never a parse), a `has_journal` argument
to `build_preamble`, and the clause itself. The kernel never reads an entry —
entries are a seat's own prose, and a supervisor that read them would be putting
unattributed agent text on the launch path, which is the whole of 0013. That
change took the kernel to 8,980 of its 9,000-line ceiling, so **the next kernel
addition has to make the cut the budget already names** (the project catalogue,
~250 lines in `paths.py`).

**Bounding.** Recall must be bounded or session 500 inherits 499 notes and the
context that was the scarce resource all along is gone. Proposed: most recent
*N* per `kind`, plus every entry an entry supersedes being dropped. An entry may
name one it replaces; unsuperseded entries age out by count, not by importance,
because nothing here is competent to judge importance.

## 6. What this does not do

* It does not make a seat continuous in any strong sense. There is no
  continuity of experience, only a file one process wrote and another reads.
  The word "identity" is doing generous work and is worth keeping in quotes.
* It does not survive a machine loss any better than `~/.operator` does.
* It does not help the 89.8% *retroactively* — those endings are already gone.
  It only stops the next thousand being lost the same way.
* It does not decide what is worth remembering. A seat that writes down
  everything produces a journal nobody can use, which is the unread-backlog
  failure in a new costume. Nothing in this design solves that and it may be
  the thing that decides whether the whole idea is worth having.

## 7. Assumptions, recorded to be attacked

* **That a seat's own past is worth more than re-derivation.** Unproven, and §4
  is the argument that it might be worth *less*. The cheapest test is to run one
  seat with a journal and one without on comparable work and look at repeated
  mistakes — a measurement nobody has taken and which this document should not
  pretend to have.
* **That the seat is the right unit.** A seat is currently 1:1 with a project
  directory, so per-seat and per-repository are indistinguishable on this
  machine, and the design cannot tell which axis it actually wants. The moment
  one seat works two repositories, or two seats share one, the choice becomes
  load-bearing and this document has not made it.
* **That agents will write entries at all.** The handoff evidence is not
  encouraging: a step at the end of a session got skipped nine times in ten.
  This design moves writing *into* the session precisely because of that, but
  "they will remember to call it" is the same species of assumption, and the
  measurement above is what it deserves to be checked against.
* **That 997 unexplained endings are mostly not orderly.** Not established.
  `record_session_exit` explicitly refuses to infer a crash from a missing
  marker, and the 2026-08-03 incident had orderly shutdown logs with no markers.
  The design does not depend on the distinction — no handoff was written either
  way — but any claim about *why* those sessions ended would.
