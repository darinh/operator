# operator

Supervision for fleets of autonomous coding agents.

You give an agent a repository and walk away. `operator` is what notices when it stops making
progress, restarts it, brings it back after a reboot, and keeps a tamper-evident record of what
happened. It writes no code itself.

The unit it supervises is a **seat**, not a session. A seat outlives the sessions it runs, keeps
its own memory across them, and accumulates knowledge of a project the longer it works there.

## Install

```
pip install -e .
operator doctor
```

`doctor` names anything missing and how to install it. It needs the GitHub Copilot CLI on PATH
and a terminal multiplexer (`tmux`, or `psmux` on Windows).

Python 3.10 or newer. Nothing else is required. The tool stands alone.

## Use it

Run `operator` with no arguments and you get a menu. Every menu action prints the command it
ran, so the flags are learnable by use rather than by memorising.

```
operator                      the menu
operator doctor               is this machine ready
operator project register     let this directory hold seat memory
operator start --name alpha   start a supervised seat here
operator list                 what is running
operator join alpha           attach your terminal to it
operator stop alpha           ask its supervisor to stop
operator recover              bring back seats a crash or reboot took down
operator trace                what happened, newest first
operator verify               has the record been altered
```

## What it actually does

**Stops a seat that has stopped working.** After each session it fingerprints the whole
repository, every ref and every worktree, and asks whether anything changed. Three consecutive
sessions that change nothing exits with `EXIT_NO_PROGRESS`. Five sessions that end without a
handoff or an observed exit exits with `EXIT_UNACCOUNTED`. It polls every 10 seconds and treats
a session shorter than 120 seconds as a crash rather than a working session.

**Knows the difference between "nothing changed" and "I could not tell."** An unreadable git
probe is `unknown`, not `unchanged`, and it does not count toward the limit. A breaker that
fires on its own blindness would stop a healthy fleet.

**Survives a reboot.** `operator recover` finds seats that were supervised when the machine
stopped and restarts their supervisors, continuing the session numbering rather than starting
over.

**Keeps a record you can check.** Every supervision fact appends to `~/.operator/trace.jsonl`
with a per-writer hash chain, so `operator verify` can tell you whether records were removed or
edited. Read `docs/ledger.md` before trusting that: it is a checksum, not tamper-evidence, and
it detects nothing against anyone with write access to the file.

**Remembers.** A seat records decisions, gotchas and dispositions with `operator remember`, and
reads them back with `operator recall`. That memory is per seat and per project, keyed on the
seat's identity rather than any session, and it is the reason a seat gets better at a repository
over time.

## Extensions

Three ship with the tool and all three are **inert until you enable them**.

```
operator ext list
operator ext enable worktree-janitor
```

That default is deliberate. Extensions answer `admit_launch`, which can refuse to start a seat,
so an extension that went live on install would be one `pip install` away from holding an entire
fleet closed. Enabling stays an explicit act.

Extension code never runs inside a supervisor process. Each call gets its own worker process
with a deadline, because cancelling a thread blocked in native code is not possible on Windows,
which makes an in-process deadline aspirational. An extension that errors is recorded as having
errored, and is never treated as having said no.

It is not a sandbox and is never described as one. A worker runs as you and can read what you
can read. `docs/extensions.md` has the full contract.

## Does a change to this make it better or worse

`operator_bench` runs the real supervisor against scripted seats whose true behaviour is known,
and scores its decisions. No LLM, no API spend, six scenarios in about 16 seconds.

```
python -m operator_bench measure
```

Every number carries its sample size, coverage, label source and an interval, or reports
`NoEstimate` with a reason. Miss rate and false-alarm rate are reported separately and never
merged, because lowering the stall threshold improves one and destroys the other.

One scenario, `backlog-0014`, is a seat that manufactures busywork. It moves the fingerprint
every session while doing nothing real, and the supervisor cannot currently tell. That scenario
scores a **miss** on purpose, and a test asserts it. When a breaker learns to see manufactured
work, that test fails and the recorded baseline changes.

## What it does not do

Stated plainly, because each of these looks built from the outside.

**Seats are not told what to work on.** `work_seam.set_session_store` is called by nothing, so
`session_store()` returns `None` on every run. A seat is supervised, not assigned. Deciding what
an agent should work on sits on the far side of this boundary and needs a work store that does
not exist here.

**There is no intent layer.** Nothing traces a change back to an authorised goal. The evidence
ledger records what happened, not whether anyone asked for it.

**There is no trust boundary.** Agents run under your filesystem identity. A seat can write the
mandate, the gate code and the ledger. Making "an agent cannot grant itself authority" true
rather than decorative needs a separate OS account and ACLs.

**Two extension hooks are declared and never asked.** `gate_change` and `detect_repo` are part
of the documented hook set with no call site, because there is no kernel merge gate to hang the
first on. Only `admit_launch` is live.

**Quality is not measured.** The progress breaker detects motion, never merit. A seat committing
plausible slop forever looks healthy.

## Layout

| | |
| --- | --- |
| `operator_kernel/` | supervision. The loop, the breakers, the ledger, the launch gate |
| `operator_fleet/` | the fleet host, which tails the ledger and drains a proposal queue |
| `operator_cli/` | entry points, including the menu |
| `operator_extensions/` | the three reference extensions |
| `operator_memory/` | the seat journal |
| `operator_bench/` | the benchmark harness |

The kernel may not import the fleet, and nothing may import the bench. Each package has a line
budget enforced by a boundary test, so adding code sometimes means extracting a seam first
rather than raising a number.

## Contributing

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.

Before opening a PR, read `.github/skills/pr-gate/SKILL.md` and run
`python .github/skills/pr-gate/preflight.py --pr <number>`. Self-merge additionally needs two
reviewers from different model families to approve.

`docs/plan.md` is the design program and records what was decided and what was withdrawn.
